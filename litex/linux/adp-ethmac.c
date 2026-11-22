// SPDX-License-Identifier: GPL-2.0 OR MIT
/*
 * Driver for the Ethernet MAC with DMA of the ADP-XC7K160 LiteX SoC (litex/adp_ethmac.py): the MAC
 * core of LiteEth with a DMA front end on the coherent DMA bus of NaxRiscv.
 *
 * Receive: the driver gives the engine free buffers through a FIFO. The engine writes each frame
 * into the oldest buffer, 2 bytes after its start, and pushes the length and the sum of the frame
 * into a completion FIFO. The driver builds the skb around the buffer, with CHECKSUM_COMPLETE.
 *
 * Transmit: the driver pushes one descriptor for each piece of a frame. The engine reads the
 * pieces into a frame buffer, inserts the checksum that CHECKSUM_PARTIAL asks for, and sends the
 * frame. For TSO the driver writes the headers of each segment into a coherent buffer, and the
 * payload pieces point into the skb. tx_done counts the frames sent.
 *
 * PTP: the SoC of the current adp_ethmac.py has a PTP clock and stamps the SFD of each frame. The
 * engine writes the stamp of a received frame after the frame, and puts the stamp of a frame that
 * asked for one into a FIFO. The driver finds these CSRs by the size of the register range.
 *
 * Filter: the engine passes the frames to the station address, three more unicast addresses,
 * broadcast, and the multicast addresses of a hash of 64 bits.
 *
 * Link: the PHY is on the bit-banged MDIO bus of LiteEth (a second register range), and phylib
 * polls it: the SoC does not connect the interrupt pin of the PHY. The MAC has no CSMA/CD and no
 * pause frames, so the driver removes the half duplex modes and the pause modes, and keeps the
 * carrier off on a half duplex link. A DT without the MDIO range gives the old behaviour: the
 * carrier is on while the interface is up.
 *
 * The CSR offsets follow the order of the CSRs in adp_ethmac.py. litex/linux/mkdts.py checks
 * them against csr.json when it makes the node.
 *
 * litex/linux/README.md of the ADP-XC7K160 board notes says how to build it.
 */

#include <linux/dma-mapping.h>
#include <linux/crc32.h>
#include <linux/etherdevice.h>
#include <linux/hrtimer.h>
#include <linux/ethtool.h>
#include <linux/if_vlan.h>
#include <linux/interrupt.h>
#include <linux/litex.h>
#include <linux/mdio-bitbang.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/of_mdio.h>
#include <linux/of_net.h>
#include <linux/phy.h>
#include <linux/platform_device.h>
#include <linux/ptp_clock_kernel.h>
#include <linux/skbuff.h>
#include <linux/tcp.h>
#include <linux/udp.h>
#include <linux/unaligned.h>
#include <net/checksum.h>
#include <net/ip6_checksum.h>
#include <net/tso.h>

#define ETH_RX_BUF		0x00
#define ETH_RX_BUFS		0x04
#define ETH_RX_STAT		0x08	/* bit 31 ready, bit 16 error, bits 10-0 length */
#define ETH_RX_CSUM		0x0c
#define ETH_RX_POP		0x10
#define ETH_RX_DROPPED		0x14
#define ETH_TX_ADDR		0x18
#define ETH_TX_CTRL		0x1c
#define ETH_TX_ROOM		0x20
#define ETH_TX_DONE		0x24
#define ETH_EV_STATUS		0x28
#define ETH_EV_PENDING		0x2c
#define ETH_EV_ENABLE		0x30
#define ETH_PREAMBLE_ERRORS	0x38
#define ETH_CRC_ERRORS		0x3c
#define ETH_REGS_LEN		0x40

/* The CSRs after the MAC core: PTP clock, TX stamps, filter, receive diagnostics. */
#define ETH_PTP_CTRL		0x40	/* write: bit 0 snap, bit 1 set, bit 2 adjust */
#define ETH_PTP_SNAP_SEC_HI	0x44
#define ETH_PTP_SNAP_SEC	0x48
#define ETH_PTP_SNAP_NS		0x4c
#define ETH_PTP_SET_SEC_HI	0x50
#define ETH_PTP_SET_SEC		0x54
#define ETH_PTP_SET_NS		0x58
#define ETH_PTP_ADJ		0x5c
#define ETH_PTP_INCR_HI		0x60
#define ETH_PTP_INCR		0x64
#define ETH_PTP_INCR_NOM_HI	0x68
#define ETH_PTP_INCR_NOM	0x6c
#define ETH_TXTS_STAT		0x70	/* bit 31 ready, bits 15-0 the frame */
#define ETH_TXTS_SEC		0x74
#define ETH_TXTS_NS		0x78
#define ETH_TXTS_POP		0x7c
#define ETH_TXTS_LOST		0x80
#define ETH_FILT_CTRL		0x84
#define ETH_FILT_ADDR_HI(i)	(0x88 + 8 * (i))
#define ETH_FILT_ADDR(i)	(0x8c + 8 * (i))
#define ETH_FILT_HASH_HI	0xa8
#define ETH_FILT_HASH		0xac
#define ETH_FILT_DROPPED	0xb0
#define ETH_RXDIAG_NO_SFD	0xb4
#define ETH_RXDIAG_BAD_PRE	0xb8
#define ETH_RXDIAG_DRIBBLE	0xbc
#define ETH_RXDIAG_LAST		0xc0
#define ETH_REGS_LEN_PTP	0xc4

#define PTP_CTRL_SNAP		BIT(0)
#define PTP_CTRL_SET		BIT(1)
#define PTP_CTRL_ADJ		BIT(2)
#define TXTS_READY		BIT(31)
#define FILT_PROMISC		BIT(0)
#define FILT_ALLMULTI		BIT(1)
#define FILT_BROADCAST		BIT(2)
#define FILT_ADDR_EN(i)		BIT(4 + (i))
#define FILT_ADDRS		4

/*
 * The stamps are a time after the SFD at the MII. The receive path stamps the SFD one MII clock
 * after the edge that sampled it; the PHY samples the SFD of a sent frame one MII clock after the
 * stamp. The stamp itself is about 2 ns before the edge of its toggle (the simulation of
 * adp_ethmac.py).
 *
 * The driver also moves the stamps from the MII to the wire with the typical latencies of the
 * DM9161C datasheet (DM9161C-DS-F01, 9.4.7, 9.4.9, 9.4.11 and 9.4.13), in bit times: 8 to send
 * and 15 to receive at 100 Mbit/s, 2 to send and 7 to receive at 10 Mbit/s. The datasheet does not
 * guarantee them.
 */
#define STAMP_EARLY_NS		2
#define PHY_TX_NS_100		80
#define PHY_RX_NS_100		150
#define PHY_TX_NS_10		200
#define PHY_RX_NS_10		700

/* The MDIO CSRs of LiteEth, in the second register range. */
#define MDIO_W			0x00
#define MDIO_R			0x04
#define MDIO_W_MDC		BIT(0)
#define MDIO_W_OE		BIT(1)
#define MDIO_W_DATA		BIT(2)

#define RX_STAT_READY		BIT(31)
#define RX_STAT_ERROR		BIT(16)
#define RX_STAT_LENGTH		GENMASK(10, 0)

#define TX_CTRL_LAST		BIT(11)
#define TX_CTRL_CSUM		BIT(12)
#define TX_CTRL_UDP		BIT(13)
#define TX_CTRL_TS		BIT(14)
#define TX_CTRL_START(x)	((x) << 16)
#define TX_CTRL_OFFSET(x)	((x) << 24)

#define EV_RX			BIT(0)
#define EV_TX			BIT(1)

#define MAX_FRAME		1536
#define RX_BUFS			256	/* the depth of the free buffer FIFO */
#define RX_DATA			(MAX_FRAME + 16)
#define RX_HEADROOM		NET_SKB_PAD
#define RX_BUF_SIZE		(SKB_DATA_ALIGN(RX_HEADROOM + RX_DATA) + \
				 SKB_DATA_ALIGN(sizeof(struct skb_shared_info)))
#define TX_DESCS		64	/* the depth of the descriptor FIFO */
#define TX_FRAMES		256	/* frames in flight; also the number of header buffers */
#define TX_HDR_SIZE		256
#define TX_SKBS			64
#define NAPI_WEIGHT		64

struct adp_eth_rx_buf {
	void *data;
	dma_addr_t dma;
};

/* An skb in flight: its DMA mappings, and the number of its frames. */
struct adp_eth_tx_skb {
	struct sk_buff *skb;
	unsigned int frames;
	dma_addr_t head;
	dma_addr_t frag[MAX_SKB_FRAGS];
};

/* Counters of the driver for ethtool -S. Each has one writer: the poll or the xmit. */
struct adp_eth_sw_stats {
	u64 rx_alloc_errors;
	u64 tx_csum_partial;
	u64 tx_tso_skbs;
	u64 tx_tso_segments;
	u64 tx_queue_stops;
	u64 tx_timeouts;
	u64 tx_hwtstamp_skipped;	/* asked while another stamp was pending */
	u64 tx_hwtstamp_timeouts;
};

/*
 * Two counters of the DM9161C (DM9161C-DS-F01, 8.14 and 8.15): RXER events in register 22 and
 * link drops in register 23. A read clears them, so the driver adds them up. Another reader of
 * these registers (for example mii-tool -vv) takes the counts away from ethtool -S.
 */
#define DM9161C_PHY_ID		0x0181b8b0
#define DM9161C_PHY_ID_MASK	0xfffffff0
#define DM9161C_RECR		22
#define DM9161C_DISCR		23

struct adp_eth {
	void __iomem *base;
	struct net_device *netdev;
	struct device *dev;
	struct napi_struct napi;

	/* MDIO and PHY; mii is NULL without the MDIO range in the DT. */
	void __iomem *mdio_base;
	struct mdiobb_ctrl bb;
	u32 mdio_w;			/* the value of the MDIO_W CSR */
	struct mii_bus *mii;
	struct device_node *phy_node;

	struct adp_eth_sw_stats sw;
	u64 phy_rx_errors;		/* under RTNL, as the ethtool calls */
	u64 phy_disconnects;

	/* PTP; has_ptp is false with a SoC of an older adp_ethmac.py. */
	bool has_ptp;
	struct ptp_clock *ptp;
	struct ptp_clock_info ptp_info;
	spinlock_t ptp_lock;		/* the sequences of PTP_CTRL and the snapshot */
	u64 incr_nom;
	u32 sec_hi;			/* bits 47-32 of the seconds, at the last read or set */
	struct kernel_hwtstamp_config ts_cfg;
	spinlock_t ts_lock;		/* ts_skb and the TX stamp FIFO */
	struct sk_buff *ts_skb;		/* the skb that waits for its TX stamp */
	struct hrtimer ts_timer;	/* polls for the stamp of ts_skb */
	u16 ts_index;			/* its frame, as the engine counts the frames */
	unsigned long ts_start;		/* jiffies at its xmit */
	bool tx_skb_ts;			/* tx_skb asks for a stamp */

	/* Receive: the buffers that the engine holds, oldest first. */
	struct adp_eth_rx_buf rx[RX_BUFS];
	unsigned int rx_head;		/* the oldest buffer */
	unsigned int rx_count;
	bool started;			/* the engine has buffers from an open */

	/* Transmit */
	u8 *hdr;			/* TX_FRAMES header buffers */
	dma_addr_t hdr_dma;
	u32 tx_frames;			/* frames pushed */
	u32 tx_sent;			/* frames sent, as far as tx_done showed */
	struct adp_eth_tx_skb tx[TX_SKBS];
	unsigned int tx_skb_head;	/* the next free entry */
	unsigned int tx_skb_tail;	/* the oldest entry in flight */
	unsigned int tx_skb_count;
	struct sk_buff *tx_skb;		/* the skb that has frames left to push */
	struct tso_t tso;
	unsigned int tso_left;		/* payload bytes of tx_skb not pushed */
	unsigned int tx_room;		/* free descriptors, as last read */
};

static inline u32 adp_rd(struct adp_eth *priv, unsigned int reg)
{
	return litex_read32(priv->base + reg);
}

static inline void adp_wr(struct adp_eth *priv, unsigned int reg, u32 val)
{
	litex_write32(priv->base + reg, val);
}

/* PTP clock -------------------------------------------------------------------------------------- */

static bool adp_eth_is_10(struct adp_eth *priv)
{
	struct phy_device *phydev = priv->netdev->phydev;

	return phydev && phydev->speed == SPEED_10;
}

/* From the stamp to the SFD on the wire: one MII clock and the latency of the PHY. */
static s64 adp_eth_tx_corr(struct adp_eth *priv)
{
	return (adp_eth_is_10(priv) ? 400 + PHY_TX_NS_10 : 40 + PHY_TX_NS_100) + STAMP_EARLY_NS;
}

static s64 adp_eth_rx_corr(struct adp_eth *priv)
{
	return -(adp_eth_is_10(priv) ? 400 + PHY_RX_NS_10 : 40 + PHY_RX_NS_100) + STAMP_EARLY_NS;
}

static ktime_t adp_eth_stamp(struct adp_eth *priv, u32 sec_lo, u32 ns, s64 corr)
{
	u64 sec = ((u64)READ_ONCE(priv->sec_hi) << 32) | sec_lo;

	return ns_to_ktime(sec * NSEC_PER_SEC + ns + corr);
}

static int adp_eth_ptp_gettimex(struct ptp_clock_info *info, struct timespec64 *ts,
				struct ptp_system_timestamp *sts)
{
	struct adp_eth *priv = container_of(info, struct adp_eth, ptp_info);
	unsigned long flags;
	u32 hi, sec, ns;

	spin_lock_irqsave(&priv->ptp_lock, flags);
	ptp_read_system_prets(sts);
	adp_wr(priv, ETH_PTP_CTRL, PTP_CTRL_SNAP);
	ptp_read_system_postts(sts);
	hi = adp_rd(priv, ETH_PTP_SNAP_SEC_HI);
	sec = adp_rd(priv, ETH_PTP_SNAP_SEC);
	ns = adp_rd(priv, ETH_PTP_SNAP_NS);
	WRITE_ONCE(priv->sec_hi, hi);
	spin_unlock_irqrestore(&priv->ptp_lock, flags);

	ts->tv_sec = ((u64)hi << 32) | sec;
	ts->tv_nsec = ns;
	return 0;
}

static int adp_eth_ptp_settime(struct ptp_clock_info *info, const struct timespec64 *ts)
{
	struct adp_eth *priv = container_of(info, struct adp_eth, ptp_info);
	unsigned long flags;

	spin_lock_irqsave(&priv->ptp_lock, flags);
	adp_wr(priv, ETH_PTP_SET_SEC_HI, upper_32_bits(ts->tv_sec));
	adp_wr(priv, ETH_PTP_SET_SEC, lower_32_bits(ts->tv_sec));
	adp_wr(priv, ETH_PTP_SET_NS, ts->tv_nsec);
	adp_wr(priv, ETH_PTP_CTRL, PTP_CTRL_SET);
	WRITE_ONCE(priv->sec_hi, upper_32_bits(ts->tv_sec));
	spin_unlock_irqrestore(&priv->ptp_lock, flags);
	return 0;
}

/* The clock adds less than one second in one step; a larger step reads and sets the time. */
static int adp_eth_ptp_adjtime(struct ptp_clock_info *info, s64 delta)
{
	struct adp_eth *priv = container_of(info, struct adp_eth, ptp_info);
	struct timespec64 ts;
	unsigned long flags;

	if (delta > -NSEC_PER_SEC && delta < NSEC_PER_SEC) {
		spin_lock_irqsave(&priv->ptp_lock, flags);
		adp_wr(priv, ETH_PTP_ADJ, (u32)(s32)delta);
		adp_wr(priv, ETH_PTP_CTRL, PTP_CTRL_ADJ);
		spin_unlock_irqrestore(&priv->ptp_lock, flags);
		return 0;
	}
	adp_eth_ptp_gettimex(info, &ts, NULL);
	ts = timespec64_add(ts, ns_to_timespec64(delta));
	return adp_eth_ptp_settime(info, &ts);
}

static int adp_eth_ptp_adjfine(struct ptp_clock_info *info, long scaled_ppm)
{
	struct adp_eth *priv = container_of(info, struct adp_eth, ptp_info);
	u64 incr = adjust_by_scaled_ppm(priv->incr_nom, scaled_ppm);
	unsigned long flags;

	/* incr_hi is the integer part of the nanoseconds; it changes only for large adjustments. */
	spin_lock_irqsave(&priv->ptp_lock, flags);
	if (upper_32_bits(incr) != adp_rd(priv, ETH_PTP_INCR_HI))
		adp_wr(priv, ETH_PTP_INCR_HI, upper_32_bits(incr));
	adp_wr(priv, ETH_PTP_INCR, lower_32_bits(incr));
	spin_unlock_irqrestore(&priv->ptp_lock, flags);
	return 0;
}

/* Give ts_skb its TX stamp when the FIFO has it. The caller holds ts_lock. */
static void adp_eth_txts_poll(struct adp_eth *priv)
{
	while (priv->ts_skb) {
		struct skb_shared_hwtstamps hwts = { };
		u32 stat = adp_rd(priv, ETH_TXTS_STAT);
		u32 sec, ns;

		if (!(stat & TXTS_READY)) {
			if (time_after(jiffies, priv->ts_start + HZ)) {
				dev_kfree_skb_any(priv->ts_skb);
				priv->ts_skb = NULL;
				priv->sw.tx_hwtstamp_timeouts++;
			}
			return;
		}
		sec = adp_rd(priv, ETH_TXTS_SEC);
		ns = adp_rd(priv, ETH_TXTS_NS);
		adp_wr(priv, ETH_TXTS_POP, 1);
		if ((u16)stat != priv->ts_index)
			continue;	/* a stamp of a skb that timed out */
		hwts.hwtstamp = adp_eth_stamp(priv, sec, ns, adp_eth_tx_corr(priv));
		skb_tstamp_tx(priv->ts_skb, &hwts);
		dev_kfree_skb_any(priv->ts_skb);
		priv->ts_skb = NULL;
	}
}

/*
 * The xmit of a frame with a stamp starts a timer that polls the TX stamp FIFO each 100 us, until
 * the stamp comes or adp_eth_txts_poll() drops the skb after 1 s. The poll cannot wait for the tx
 * event: when the queue is idle the event is off, and it also comes when the last byte of a frame
 * leaves the frame buffer, before the SFD of a short frame is on the MII.
 */
#define TXTS_POLL_NS		(100 * NSEC_PER_USEC)

static enum hrtimer_restart adp_eth_ts_timer(struct hrtimer *timer)
{
	struct adp_eth *priv = container_of(timer, struct adp_eth, ts_timer);
	bool pending;

	spin_lock(&priv->ts_lock);
	adp_eth_txts_poll(priv);
	pending = priv->ts_skb;
	spin_unlock(&priv->ts_lock);
	if (!pending)
		return HRTIMER_NORESTART;
	hrtimer_forward_now(timer, ns_to_ktime(TXTS_POLL_NS));
	return HRTIMER_RESTART;
}

/* The aux worker keeps the high bits of the seconds. */
static long adp_eth_ptp_aux(struct ptp_clock_info *info)
{
	struct timespec64 ts;

	adp_eth_ptp_gettimex(info, &ts, NULL);
	return HZ;
}

static const struct ptp_clock_info adp_eth_ptp_info = {
	.owner		= THIS_MODULE,
	.name		= "adp-ethmac",
	.max_adj	= 500000,
	.gettimex64	= adp_eth_ptp_gettimex,
	.settime64	= adp_eth_ptp_settime,
	.adjtime	= adp_eth_ptp_adjtime,
	.adjfine	= adp_eth_ptp_adjfine,
	.do_aux_work	= adp_eth_ptp_aux,
};

static int adp_eth_hwtstamp_get(struct net_device *netdev, struct kernel_hwtstamp_config *cfg)
{
	struct adp_eth *priv = netdev_priv(netdev);

	if (!priv->has_ptp)
		return -EOPNOTSUPP;
	cfg->tx_type = priv->ts_cfg.tx_type;
	cfg->rx_filter = priv->ts_cfg.rx_filter;
	return 0;
}

/* The engine stamps every frame: each receive filter becomes HWTSTAMP_FILTER_ALL. */
static int adp_eth_hwtstamp_set(struct net_device *netdev, struct kernel_hwtstamp_config *cfg,
				struct netlink_ext_ack *extack)
{
	struct adp_eth *priv = netdev_priv(netdev);

	if (!priv->has_ptp)
		return -EOPNOTSUPP;
	if (cfg->tx_type != HWTSTAMP_TX_OFF && cfg->tx_type != HWTSTAMP_TX_ON)
		return -ERANGE;
	if (cfg->rx_filter != HWTSTAMP_FILTER_NONE)
		cfg->rx_filter = HWTSTAMP_FILTER_ALL;
	priv->ts_cfg.tx_type = cfg->tx_type;
	priv->ts_cfg.rx_filter = cfg->rx_filter;
	return 0;
}

/* Address filter --------------------------------------------------------------------------------- */

static void adp_eth_filt_addr(struct adp_eth *priv, int i, const u8 *a)
{
	adp_wr(priv, ETH_FILT_ADDR_HI(i), a[0] << 8 | a[1]);
	adp_wr(priv, ETH_FILT_ADDR(i), get_unaligned_be32(a + 2));
}

static void adp_eth_set_rx_mode(struct net_device *netdev)
{
	struct adp_eth *priv = netdev_priv(netdev);
	struct netdev_hw_addr *ha;
	u32 ctrl = FILT_BROADCAST | FILT_ADDR_EN(0);
	u64 hash = 0;
	int i = 1;

	if (!priv->has_ptp)
		return;
	adp_eth_filt_addr(priv, 0, netdev->dev_addr);
	if (netdev->flags & IFF_PROMISC || netdev_uc_count(netdev) > FILT_ADDRS - 1) {
		ctrl |= FILT_PROMISC;
	} else {
		netdev_for_each_uc_addr(ha, netdev) {
			adp_eth_filt_addr(priv, i, ha->addr);
			ctrl |= FILT_ADDR_EN(i++);
		}
	}
	if (netdev->flags & IFF_ALLMULTI)
		ctrl |= FILT_ALLMULTI;
	netdev_for_each_mc_addr(ha, netdev)
		hash |= BIT_ULL(ether_crc_le(ETH_ALEN, ha->addr) >> 26);
	adp_wr(priv, ETH_FILT_HASH_HI, upper_32_bits(hash));
	adp_wr(priv, ETH_FILT_HASH, lower_32_bits(hash));
	adp_wr(priv, ETH_FILT_CTRL, ctrl);
}

static int adp_eth_set_mac_address(struct net_device *netdev, void *addr)
{
	int err = eth_mac_addr(netdev, addr);

	if (!err)
		adp_eth_set_rx_mode(netdev);
	return err;
}

/* Receive ---------------------------------------------------------------------------------------- */

static int adp_eth_rx_give(struct adp_eth *priv)
{
	struct adp_eth_rx_buf *b = &priv->rx[(priv->rx_head + priv->rx_count) % RX_BUFS];

	b->data = napi_alloc_frag(RX_BUF_SIZE);
	if (!b->data)
		return -ENOMEM;
	b->dma = dma_map_single(priv->dev, b->data + RX_HEADROOM, RX_DATA, DMA_FROM_DEVICE);
	if (dma_mapping_error(priv->dev, b->dma)) {
		skb_free_frag(b->data);
		return -ENOMEM;
	}
	adp_wr(priv, ETH_RX_BUF, b->dma);
	priv->rx_count++;
	return 0;
}

/* Give the oldest buffer back to the engine, after a dropped frame. */
static void adp_eth_rx_recycle(struct adp_eth *priv, struct adp_eth_rx_buf *b)
{
	struct adp_eth_rx_buf *n = &priv->rx[(priv->rx_head + priv->rx_count) % RX_BUFS];

	*n = *b;
	dma_sync_single_for_device(priv->dev, n->dma, RX_DATA, DMA_FROM_DEVICE);
	adp_wr(priv, ETH_RX_BUF, n->dma);
	priv->rx_count++;
}

static int adp_eth_rx(struct adp_eth *priv, int budget)
{
	struct net_device *netdev = priv->netdev;
	int done = 0;

	while (done < budget) {
		u32 stat = adp_rd(priv, ETH_RX_STAT);
		struct adp_eth_rx_buf b;
		unsigned int len;
		struct sk_buff *skb;
		__wsum csum;

		if (!(stat & RX_STAT_READY))
			break;
		csum = (__force __wsum)adp_rd(priv, ETH_RX_CSUM);
		adp_wr(priv, ETH_RX_POP, 1);
		done++;

		b = priv->rx[priv->rx_head];
		priv->rx_head = (priv->rx_head + 1) % RX_BUFS;
		priv->rx_count--;
		len = stat & RX_STAT_LENGTH;

		if ((stat & RX_STAT_ERROR) || len < ETH_HLEN) {
			netdev->stats.rx_errors++;
			adp_eth_rx_recycle(priv, &b);
			continue;
		}
		/* A new buffer first: if there is none, the frame is dropped, and its buffer stays. */
		if (adp_eth_rx_give(priv)) {
			netdev->stats.rx_dropped++;
			priv->sw.rx_alloc_errors++;
			adp_eth_rx_recycle(priv, &b);
			continue;
		}
		dma_unmap_single(priv->dev, b.dma, RX_DATA, DMA_FROM_DEVICE);
		skb = napi_build_skb(b.data, RX_BUF_SIZE);
		if (!skb) {
			skb_free_frag(b.data);
			netdev->stats.rx_dropped++;
			continue;
		}
		skb_reserve(skb, RX_HEADROOM + NET_IP_ALIGN);
		skb_put(skb, len);

		/* The stamp follows the frame at the next 8-byte boundary of the buffer. */
		if (priv->ts_cfg.rx_filter != HWTSTAMP_FILTER_NONE) {
			const __le32 *t = b.data + RX_HEADROOM + ALIGN(NET_IP_ALIGN + len, 8);

			skb_hwtstamps(skb)->hwtstamp = adp_eth_stamp(priv, le32_to_cpu(t[1]),
				le32_to_cpu(t[0]), adp_eth_rx_corr(priv));
		}

		/* The sum of the engine starts at byte 0; CHECKSUM_COMPLETE starts after the header. */
		if (netdev->features & NETIF_F_RXCSUM) {
			skb->csum = csum_sub(csum, csum_partial(skb->data, ETH_HLEN, 0));
			skb->ip_summed = CHECKSUM_COMPLETE;
		}
		skb->protocol = eth_type_trans(skb, netdev);
		dev_sw_netstats_rx_add(netdev, len);
		napi_gro_receive(&priv->napi, skb);
	}
	return done;
}

/* Transmit --------------------------------------------------------------------------------------- */

static inline void adp_eth_tx_desc(struct adp_eth *priv, dma_addr_t addr, u32 ctrl)
{
	adp_wr(priv, ETH_TX_ADDR, addr);
	adp_wr(priv, ETH_TX_CTRL, ctrl);
	priv->tx_room--;
}

/* Free the skbs whose frames are all sent. */
static void adp_eth_tx_reclaim(struct adp_eth *priv)
{
	u16 done = adp_rd(priv, ETH_TX_DONE);
	unsigned int sent = (u16)(done - (u16)priv->tx_sent);
	unsigned int pkts = 0, bytes = 0;

	priv->tx_sent += sent;
	while (priv->tx_skb_count) {
		struct adp_eth_tx_skb *t = &priv->tx[priv->tx_skb_tail];
		struct sk_buff *skb = t->skb;
		int i;

		/* The entry of tx_skb has frames that are not pushed yet. */
		if (skb == priv->tx_skb || t->frames > sent)
			break;
		sent -= t->frames;
		t->frames = 0;
		dma_unmap_single(priv->dev, t->head, skb_headlen(skb), DMA_TO_DEVICE);
		for (i = 0; i < skb_shinfo(skb)->nr_frags; i++)
			dma_unmap_page(priv->dev, t->frag[i], skb_frag_size(&skb_shinfo(skb)->frags[i]),
				       DMA_TO_DEVICE);
		pkts++;
		bytes += skb->len;
		napi_consume_skb(skb, 1);
		t->skb = NULL;
		priv->tx_skb_tail = (priv->tx_skb_tail + 1) % TX_SKBS;
		priv->tx_skb_count--;
	}
	/* Frames of the oldest skb that are sent: count them off. */
	if (priv->tx_skb_count)
		priv->tx[priv->tx_skb_tail].frames -= sent;
}

static int adp_eth_tx_map(struct adp_eth *priv, struct sk_buff *skb, struct adp_eth_tx_skb *t)
{
	int i;

	t->head = dma_map_single(priv->dev, skb->data, skb_headlen(skb), DMA_TO_DEVICE);
	if (dma_mapping_error(priv->dev, t->head))
		return -ENOMEM;
	for (i = 0; i < skb_shinfo(skb)->nr_frags; i++) {
		const skb_frag_t *f = &skb_shinfo(skb)->frags[i];

		t->frag[i] = skb_frag_dma_map(priv->dev, f, 0, skb_frag_size(f), DMA_TO_DEVICE);
		if (dma_mapping_error(priv->dev, t->frag[i]))
			goto err;
	}
	return 0;
err:
	while (i--)
		dma_unmap_page(priv->dev, t->frag[i], skb_frag_size(&skb_shinfo(skb)->frags[i]),
			       DMA_TO_DEVICE);
	dma_unmap_single(priv->dev, t->head, skb_headlen(skb), DMA_TO_DEVICE);
	return -ENOMEM;
}

/* Push an skb that is one frame: its linear part and its frags. */
static void adp_eth_tx_one(struct adp_eth *priv, struct sk_buff *skb, struct adp_eth_tx_skb *t)
{
	unsigned int nfrags = skb_shinfo(skb)->nr_frags;
	u32 csum = 0;
	int i;

	if (skb->ip_summed == CHECKSUM_PARTIAL) {
		csum = TX_CTRL_CSUM | TX_CTRL_START(skb_checksum_start_offset(skb)) |
		       TX_CTRL_OFFSET(skb->csum_offset);
		if (skb->csum_offset == offsetof(struct udphdr, check))
			csum |= TX_CTRL_UDP;
		priv->sw.tx_csum_partial++;
	}
	if (priv->tx_skb_ts) {
		csum |= TX_CTRL_TS;
		priv->ts_index = priv->tx_frames;
	}
	adp_eth_tx_desc(priv, t->head, skb_headlen(skb) | csum | (nfrags ? 0 : TX_CTRL_LAST));
	for (i = 0; i < nfrags; i++)
		adp_eth_tx_desc(priv, t->frag[i], skb_frag_size(&skb_shinfo(skb)->frags[i]) |
				(i == nfrags - 1 ? TX_CTRL_LAST : 0));
	t->frames++;
	priv->tx_frames++;
}

/* The DMA address of the payload at tso.data. */
static dma_addr_t adp_eth_tso_dma(struct adp_eth *priv, struct sk_buff *skb, struct adp_eth_tx_skb *t)
{
	int idx = priv->tso.next_frag_idx;

	if (priv->tso.data >= (void *)skb->data && priv->tso.data < (void *)skb_tail_pointer(skb))
		return t->head + (priv->tso.data - (void *)skb->data);
	return t->frag[idx - 1] + (priv->tso.data - skb_frag_address(&skb_shinfo(skb)->frags[idx - 1]));
}

/*
 * Push the next segment of a TSO skb: its headers from tso_build_hdr() in a header buffer, and its
 * payload pieces. The TCP checksum field holds the sum of the pseudo-header; the engine adds the
 * segment, as for CHECKSUM_PARTIAL.
 */
static void adp_eth_tx_tso_segment(struct adp_eth *priv, struct sk_buff *skb, struct adp_eth_tx_skb *t)
{
	unsigned int slot = priv->tx_frames % TX_FRAMES;
	u8 *hdr = priv->hdr + slot * TX_HDR_SIZE;
	int toff = skb_transport_offset(skb);
	int noff = skb_network_offset(skb);
	int hdr_len = toff + priv->tso.tlen;
	unsigned int size = min(priv->tso_left, (unsigned int)skb_shinfo(skb)->gso_size);
	unsigned int tcp_len = priv->tso.tlen + size;
	unsigned int left = size;
	struct tcphdr *th;

	tso_build_hdr(skb, hdr, &priv->tso, size, size == priv->tso_left);
	th = (struct tcphdr *)(hdr + toff);
	if (!priv->tso.ipv6) {
		struct iphdr *iph = (struct iphdr *)(hdr + noff);

		ip_send_check(iph);
		th->check = ~csum_tcpudp_magic(iph->saddr, iph->daddr, tcp_len, IPPROTO_TCP, 0);
	} else {
		struct ipv6hdr *ip6h = (struct ipv6hdr *)(hdr + noff);

		th->check = ~csum_ipv6_magic(&ip6h->saddr, &ip6h->daddr, tcp_len, IPPROTO_TCP, 0);
	}
	adp_eth_tx_desc(priv, priv->hdr_dma + slot * TX_HDR_SIZE, hdr_len | TX_CTRL_CSUM |
			TX_CTRL_START(toff) | TX_CTRL_OFFSET(offsetof(struct tcphdr, check)));
	while (left) {
		unsigned int n = min_t(unsigned int, priv->tso.size, left);

		adp_eth_tx_desc(priv, adp_eth_tso_dma(priv, skb, t), n | (n == left ? TX_CTRL_LAST : 0));
		tso_build_data(skb, &priv->tso, n);
		left -= n;
	}
	priv->tso_left -= size;
	priv->sw.tx_tso_segments++;
	t->frames++;
	priv->tx_frames++;
}

/*
 * Push the frames of tx_skb while the engine has room. Return true when all of them are pushed.
 * The caller holds the lock of the queue.
 */
static bool adp_eth_tx_fill(struct adp_eth *priv)
{
	struct sk_buff *skb = priv->tx_skb;
	struct adp_eth_tx_skb *t = &priv->tx[(priv->tx_skb_head + TX_SKBS - 1) % TX_SKBS];
	unsigned int need = 2 + skb_shinfo(skb)->nr_frags;	/* the most pieces of one frame */

	priv->tx_room = adp_rd(priv, ETH_TX_ROOM);
	if (!skb_is_gso(skb)) {
		if (priv->tx_room < need || priv->tx_frames - priv->tx_sent >= TX_FRAMES)
			return false;
		adp_eth_tx_one(priv, skb, t);
	} else {
		while (priv->tso_left && priv->tx_room >= need &&
		       priv->tx_frames - priv->tx_sent < TX_FRAMES)
			adp_eth_tx_tso_segment(priv, skb, t);
		if (priv->tso_left)
			return false;
	}
	priv->tx_skb = NULL;
	return true;
}

/* Return true if the engine can take the next skb. */
static bool adp_eth_tx_room(struct adp_eth *priv)
{
	return priv->tx_skb_count < TX_SKBS && !priv->tx_skb;
}

static netdev_tx_t adp_eth_start_xmit(struct sk_buff *skb, struct net_device *netdev)
{
	struct adp_eth *priv = netdev_priv(netdev);
	struct adp_eth_tx_skb *t;

	if (unlikely(!adp_eth_tx_room(priv))) {
		netif_stop_queue(netdev);
		return NETDEV_TX_BUSY;
	}
	if (skb_is_gso(skb)) {
		int hdr_len = tso_start(skb, &priv->tso);

		priv->tso_left = skb->len - hdr_len;
		if (unlikely(hdr_len > TX_HDR_SIZE || hdr_len + skb_shinfo(skb)->gso_size > MAX_FRAME))
			goto drop;
		priv->sw.tx_tso_skbs++;
	} else if (unlikely(skb->len > MAX_FRAME)) {
		goto drop;
	}
	if (!skb_is_gso(skb) && skb->ip_summed == CHECKSUM_PARTIAL &&
	    skb_checksum_start_offset(skb) > 255 && skb_checksum_help(skb))
		goto drop;

	t = &priv->tx[priv->tx_skb_head];
	if (adp_eth_tx_map(priv, skb, t))
		goto drop;
	t->skb = skb;
	t->frames = 0;
	priv->tx_skb_head = (priv->tx_skb_head + 1) % TX_SKBS;
	priv->tx_skb_count++;
	dev_sw_netstats_tx_add(netdev, skb_is_gso(skb) ? skb_shinfo(skb)->gso_segs : 1, skb->len);

	/* One TX stamp at a time, for a frame that is one skb. */
	priv->tx_skb_ts = false;
	if (unlikely(skb_shinfo(skb)->tx_flags & SKBTX_HW_TSTAMP) && !skb_is_gso(skb) &&
	    priv->ts_cfg.tx_type == HWTSTAMP_TX_ON) {
		spin_lock(&priv->ts_lock);
		if (!priv->ts_skb) {
			skb_shinfo(skb)->tx_flags |= SKBTX_IN_PROGRESS;
			priv->ts_skb = skb_get(skb);
			priv->ts_start = jiffies;
			priv->tx_skb_ts = true;
			hrtimer_start(&priv->ts_timer, ns_to_ktime(TXTS_POLL_NS), HRTIMER_MODE_REL_SOFT);
		} else {
			priv->sw.tx_hwtstamp_skipped++;
		}
		spin_unlock(&priv->ts_lock);
	}
	skb_tx_timestamp(skb);

	priv->tx_skb = skb;
	if (!adp_eth_tx_fill(priv) || !adp_eth_tx_room(priv)) {
		/* The poll continues a stopped queue, after the event of a sent frame. */
		netif_stop_queue(netdev);
		priv->sw.tx_queue_stops++;
		adp_wr(priv, ETH_EV_ENABLE, adp_rd(priv, ETH_EV_ENABLE) | EV_TX);
	}
	return NETDEV_TX_OK;

drop:
	dev_kfree_skb_any(skb);
	netdev->stats.tx_dropped++;
	return NETDEV_TX_OK;
}

/*
 * Free the sent skbs, push the rest of tx_skb, and wake a stopped queue when the engine has room.
 * The poll holds the lock of the queue, so that it does not run together with the xmit.
 */
static void adp_eth_tx_complete(struct adp_eth *priv)
{
	struct netdev_queue *txq = netdev_get_tx_queue(priv->netdev, 0);

	__netif_tx_lock(txq, smp_processor_id());
	adp_eth_tx_reclaim(priv);
	if (priv->tx_skb)
		adp_eth_tx_fill(priv);
	if (netif_tx_queue_stopped(txq) && adp_eth_tx_room(priv))
		netif_tx_wake_queue(txq);
	__netif_tx_unlock(txq);
}

/* Interrupt and poll ----------------------------------------------------------------------------- */

static irqreturn_t adp_eth_interrupt(int irq, void *dev_id)
{
	struct adp_eth *priv = netdev_priv(dev_id);

	/*
	 * Mask both events until the poll ends, also when the poll is scheduled already: the rx
	 * event is a level, and its line would stay high.
	 */
	adp_wr(priv, ETH_EV_ENABLE, 0);
	adp_wr(priv, ETH_EV_PENDING, EV_TX);
	napi_schedule(&priv->napi);
	return IRQ_HANDLED;
}

static int adp_eth_poll(struct napi_struct *napi, int budget)
{
	struct adp_eth *priv = container_of(napi, struct adp_eth, napi);
	int done;

	adp_eth_tx_complete(priv);
	if (priv->ts_skb) {
		spin_lock(&priv->ts_lock);
		adp_eth_txts_poll(priv);
		spin_unlock(&priv->ts_lock);
	}
	done = adp_eth_rx(priv, budget);

	if (done < budget && napi_complete_done(napi, done)) {
		/* The tx event only while frames are in flight; a pending one fires at once. */
		u32 ev = EV_RX;

		if (priv->tx_skb_count)
			ev |= EV_TX;
		adp_wr(priv, ETH_EV_ENABLE, ev);
	}
	return done;
}

/* Net device ------------------------------------------------------------------------------------- */

static int adp_eth_open(struct net_device *netdev)
{
	struct adp_eth *priv = netdev_priv(netdev);
	int err;

	/*
	 * The engine cannot give its buffers back: after a close they stay with it, and the next
	 * open uses them. Only the first open after the reset of the SoC gives buffers.
	 */
	if (!priv->started) {
		if (adp_rd(priv, ETH_RX_BUFS) || (adp_rd(priv, ETH_RX_STAT) & RX_STAT_READY)) {
			netdev_err(netdev, "the engine holds buffers of an earlier driver\n");
			return -EBUSY;
		}
		local_bh_disable();
		while (priv->rx_count < RX_BUFS)
			if (adp_eth_rx_give(priv))
				break;
		local_bh_enable();
		if (!priv->rx_count)
			return -ENOMEM;
		priv->tx_sent = priv->tx_frames = (u16)adp_rd(priv, ETH_TX_DONE);
		priv->started = true;
	}

	if (priv->has_ptp) {
		adp_eth_set_rx_mode(netdev);
		while (adp_rd(priv, ETH_TXTS_STAT) & TXTS_READY)
			adp_wr(priv, ETH_TXTS_POP, 1);
	}
	adp_wr(priv, ETH_EV_PENDING, EV_TX);
	err = request_irq(netdev->irq, adp_eth_interrupt, 0, netdev->name, netdev);
	if (err)
		return err;
	napi_enable(&priv->napi);
	adp_wr(priv, ETH_EV_ENABLE, EV_RX | (priv->tx_skb_count ? EV_TX : 0));
	if (netdev->phydev)
		phy_start(netdev->phydev);
	else
		netif_carrier_on(netdev);
	netif_start_queue(netdev);
	return 0;
}

static int adp_eth_stop(struct net_device *netdev)
{
	struct adp_eth *priv = netdev_priv(netdev);

	netif_stop_queue(netdev);
	if (netdev->phydev)
		phy_stop(netdev->phydev);
	netif_carrier_off(netdev);
	adp_wr(priv, ETH_EV_ENABLE, 0);
	free_irq(netdev->irq, netdev);
	napi_disable(&priv->napi);
	/* The receive buffers and the skbs in flight stay; the next open continues with them. */
	return 0;
}

static void adp_eth_get_stats64(struct net_device *netdev, struct rtnl_link_stats64 *stats)
{
	struct adp_eth *priv = netdev_priv(netdev);

	netdev_stats_to_stats64(stats, &netdev->stats);
	dev_fetch_sw_netstats(stats, netdev->tstats);
	stats->rx_missed_errors = adp_rd(priv, ETH_RX_DROPPED);
	stats->rx_crc_errors = adp_rd(priv, ETH_CRC_ERRORS);
}

static void adp_eth_tx_timeout(struct net_device *netdev, unsigned int txqueue)
{
	struct adp_eth *priv = netdev_priv(netdev);

	netdev_warn(netdev, "transmit timeout: %u frames in flight\n", priv->tx_frames - priv->tx_sent);
	priv->sw.tx_timeouts++;
	napi_schedule(&priv->napi);
}

static const struct net_device_ops adp_eth_netdev_ops = {
	.ndo_open		= adp_eth_open,
	.ndo_stop		= adp_eth_stop,
	.ndo_start_xmit		= adp_eth_start_xmit,
	.ndo_get_stats64	= adp_eth_get_stats64,
	.ndo_tx_timeout		= adp_eth_tx_timeout,
	.ndo_set_mac_address	= adp_eth_set_mac_address,
	.ndo_set_rx_mode	= adp_eth_set_rx_mode,
	.ndo_validate_addr	= eth_validate_addr,
	.ndo_eth_ioctl		= phy_do_ioctl_running,
	.ndo_hwtstamp_get	= adp_eth_hwtstamp_get,
	.ndo_hwtstamp_set	= adp_eth_hwtstamp_set,
};

/* MDIO and PHY ----------------------------------------------------------------------------------- */

static void adp_mdio_set(struct mdiobb_ctrl *ctrl, u32 bit, int level)
{
	struct adp_eth *priv = container_of(ctrl, struct adp_eth, bb);

	if (level)
		priv->mdio_w |= bit;
	else
		priv->mdio_w &= ~bit;
	litex_write32(priv->mdio_base + MDIO_W, priv->mdio_w);
}

static void adp_mdio_set_mdc(struct mdiobb_ctrl *ctrl, int level)
{
	adp_mdio_set(ctrl, MDIO_W_MDC, level);
}

static void adp_mdio_set_dir(struct mdiobb_ctrl *ctrl, int output)
{
	adp_mdio_set(ctrl, MDIO_W_OE, output);
}

static void adp_mdio_set_data(struct mdiobb_ctrl *ctrl, int value)
{
	adp_mdio_set(ctrl, MDIO_W_DATA, value);
}

static int adp_mdio_get_data(struct mdiobb_ctrl *ctrl)
{
	struct adp_eth *priv = container_of(ctrl, struct adp_eth, bb);

	return litex_read32(priv->mdio_base + MDIO_R) & 1;
}

static const struct mdiobb_ops adp_mdio_ops = {
	.owner		= THIS_MODULE,
	.set_mdc	= adp_mdio_set_mdc,
	.set_mdio_dir	= adp_mdio_set_dir,
	.set_mdio_data	= adp_mdio_set_data,
	.get_mdio_data	= adp_mdio_get_data,
};

static void adp_eth_adjust_link(struct net_device *netdev)
{
	struct phy_device *phydev = netdev->phydev;

	/*
	 * The MAC has no CSMA/CD. A partner without autonegotiation gives a half duplex link by
	 * parallel detection; the carrier stays off on it.
	 */
	if (phydev->link && phydev->duplex != DUPLEX_FULL) {
		netif_carrier_off(netdev);
		netdev_warn(netdev, "the link is not full duplex (a partner without autonegotiation?); the carrier stays off\n");
	}
	phy_print_status(phydev);
}

/* Register the MDIO bus of the "mdio" child node, and attach the PHY of phy-handle. */
static int adp_eth_mdio_init(struct platform_device *pdev, struct adp_eth *priv)
{
	struct device_node *np = pdev->dev.of_node;
	struct device_node *mdio_np;
	struct phy_device *phydev;
	int err;

	priv->mdio_base = devm_platform_ioremap_resource(pdev, 1);
	if (IS_ERR(priv->mdio_base))
		return PTR_ERR(priv->mdio_base);
	priv->bb.ops = &adp_mdio_ops;
	priv->mii = alloc_mdio_bitbang(&priv->bb);
	if (!priv->mii)
		return -ENOMEM;
	priv->mii->name = "adp-ethmac-mdio";
	priv->mii->parent = &pdev->dev;
	snprintf(priv->mii->id, MII_BUS_ID_SIZE, "%s-mii", dev_name(&pdev->dev));

	mdio_np = of_get_child_by_name(np, "mdio");
	err = of_mdiobus_register(priv->mii, mdio_np);
	of_node_put(mdio_np);
	if (err)
		goto err_free;

	priv->phy_node = of_parse_phandle(np, "phy-handle", 0);
	phydev = of_phy_connect(priv->netdev, priv->phy_node, adp_eth_adjust_link, 0,
				PHY_INTERFACE_MODE_MII);
	if (!phydev) {
		err = dev_err_probe(&pdev->dev, -ENODEV, "no PHY at phy-handle\n");
		goto err_unregister;
	}
	phy_set_max_speed(phydev, SPEED_100);
	phy_remove_link_mode(phydev, ETHTOOL_LINK_MODE_10baseT_Half_BIT);
	phy_remove_link_mode(phydev, ETHTOOL_LINK_MODE_100baseT_Half_BIT);
	/* The MAC neither sends nor obeys pause frames. */
	phy_remove_link_mode(phydev, ETHTOOL_LINK_MODE_Pause_BIT);
	phy_remove_link_mode(phydev, ETHTOOL_LINK_MODE_Asym_Pause_BIT);
	phy_attached_info(phydev);
	return 0;

err_unregister:
	of_node_put(priv->phy_node);
	mdiobus_unregister(priv->mii);
err_free:
	free_mdio_bitbang(priv->mii);
	priv->mii = NULL;
	return err;
}

static void adp_eth_mdio_remove(struct adp_eth *priv)
{
	if (!priv->mii)
		return;
	if (priv->netdev->phydev)
		phy_disconnect(priv->netdev->phydev);
	of_node_put(priv->phy_node);
	mdiobus_unregister(priv->mii);
	free_mdio_bitbang(priv->mii);
}

/* ethtool ---------------------------------------------------------------------------------------- */

static const char adp_eth_stat_names[][ETH_GSTRING_LEN] = {
	/* Counters of the hardware */
	"rx_fifo_dropped",
	"rx_crc_errors",
	"rx_preamble_errors",
	/* Counters of the driver, in the order of struct adp_eth_sw_stats */
	"rx_alloc_errors",
	"tx_csum_partial",
	"tx_tso_skbs",
	"tx_tso_segments",
	"tx_queue_stops",
	"tx_timeouts",
	"tx_hwtstamp_skipped",
	"tx_hwtstamp_timeouts",
	/* Counters of the PHY */
	"phy_rx_errors",
	"phy_disconnects",
	/* Counters of the hardware with PTP */
	"rx_filtered",
	"rx_no_sfd",
	"rx_bad_preamble",
	"rx_odd_nibble",
	"tx_hwtstamp_lost",
};

#define ADP_ETH_HW_STATS	3
#define ADP_ETH_SW_STATS	(sizeof(struct adp_eth_sw_stats) / sizeof(u64))
#define ADP_ETH_PHY_STATS	2
#define ADP_ETH_PTP_STATS	5

static const unsigned int adp_eth_ptp_stat_regs[ADP_ETH_PTP_STATS] = {
	ETH_FILT_DROPPED, ETH_RXDIAG_NO_SFD, ETH_RXDIAG_BAD_PRE, ETH_RXDIAG_DRIBBLE, ETH_TXTS_LOST,
};

static unsigned int adp_eth_stat_count(struct adp_eth *priv)
{
	return ADP_ETH_HW_STATS + ADP_ETH_SW_STATS + ADP_ETH_PHY_STATS +
	       (priv->has_ptp ? ADP_ETH_PTP_STATS : 0);
}

static void adp_eth_get_drvinfo(struct net_device *netdev, struct ethtool_drvinfo *info)
{
	struct adp_eth *priv = netdev_priv(netdev);

	strscpy(info->driver, "adp-ethmac", sizeof(info->driver));
	strscpy(info->bus_info, dev_name(priv->dev), sizeof(info->bus_info));
}

static int adp_eth_get_sset_count(struct net_device *netdev, int sset)
{
	return sset == ETH_SS_STATS ? adp_eth_stat_count(netdev_priv(netdev)) : -EOPNOTSUPP;
}

static void adp_eth_get_strings(struct net_device *netdev, u32 sset, u8 *data)
{
	int i;

	if (sset != ETH_SS_STATS)
		return;
	for (i = 0; i < adp_eth_stat_count(netdev_priv(netdev)); i++)
		ethtool_puts(&data, adp_eth_stat_names[i]);
}

static void adp_eth_read_phy_stats(struct adp_eth *priv)
{
	struct phy_device *phydev = priv->netdev->phydev;
	int v;

	if (!phydev || (phydev->phy_id & DM9161C_PHY_ID_MASK) != DM9161C_PHY_ID)
		return;
	v = phy_read(phydev, DM9161C_RECR);
	if (v > 0)
		priv->phy_rx_errors += v;
	v = phy_read(phydev, DM9161C_DISCR);
	if (v > 0)
		priv->phy_disconnects += v & 0xff;
}

static void adp_eth_get_ethtool_stats(struct net_device *netdev, struct ethtool_stats *stats, u64 *data)
{
	struct adp_eth *priv = netdev_priv(netdev);
	const u64 *sw = (const u64 *)&priv->sw;
	unsigned int n;
	int i;

	BUILD_BUG_ON(ARRAY_SIZE(adp_eth_stat_names) !=
		     ADP_ETH_HW_STATS + ADP_ETH_SW_STATS + ADP_ETH_PHY_STATS + ADP_ETH_PTP_STATS);
	data[0] = adp_rd(priv, ETH_RX_DROPPED);
	data[1] = adp_rd(priv, ETH_CRC_ERRORS);
	data[2] = adp_rd(priv, ETH_PREAMBLE_ERRORS);
	n = ADP_ETH_HW_STATS;
	for (i = 0; i < ADP_ETH_SW_STATS; i++)
		data[n++] = READ_ONCE(sw[i]);
	adp_eth_read_phy_stats(priv);
	data[n++] = priv->phy_rx_errors;
	data[n++] = priv->phy_disconnects;
	if (priv->has_ptp)
		for (i = 0; i < ADP_ETH_PTP_STATS; i++)
			data[n++] = adp_rd(priv, adp_eth_ptp_stat_regs[i]);
}

static void adp_eth_get_ringparam(struct net_device *netdev, struct ethtool_ringparam *ring,
				  struct kernel_ethtool_ringparam *kring, struct netlink_ext_ack *extack)
{
	ring->rx_max_pending = RX_BUFS;
	ring->rx_pending = RX_BUFS;
	ring->tx_max_pending = TX_FRAMES;
	ring->tx_pending = TX_FRAMES;
}

static int adp_eth_get_regs_len(struct net_device *netdev)
{
	struct adp_eth *priv = netdev_priv(netdev);

	return priv->has_ptp ? ETH_REGS_LEN_PTP : ETH_REGS_LEN;
}

/* The CSRs of the MAC. A read has no side effect: rx_pop and the FIFO pushes act on a write. */
static void adp_eth_get_regs(struct net_device *netdev, struct ethtool_regs *regs, void *p)
{
	struct adp_eth *priv = netdev_priv(netdev);
	u32 *buf = p;
	int i;

	regs->version = priv->has_ptp ? 2 : 1;
	for (i = 0; i < adp_eth_get_regs_len(netdev) / 4; i++)
		buf[i] = adp_rd(priv, 4 * i);
}

static int adp_eth_get_ts_info(struct net_device *netdev, struct kernel_ethtool_ts_info *info)
{
	struct adp_eth *priv = netdev_priv(netdev);

	if (!priv->has_ptp)
		return ethtool_op_get_ts_info(netdev, info);
	info->so_timestamping = SOF_TIMESTAMPING_TX_SOFTWARE | SOF_TIMESTAMPING_TX_HARDWARE |
				SOF_TIMESTAMPING_RX_HARDWARE | SOF_TIMESTAMPING_RAW_HARDWARE;
	info->phc_index = ptp_clock_index(priv->ptp);
	info->tx_types = BIT(HWTSTAMP_TX_OFF) | BIT(HWTSTAMP_TX_ON);
	info->rx_filters = BIT(HWTSTAMP_FILTER_NONE) | BIT(HWTSTAMP_FILTER_ALL);
	return 0;
}

static const struct ethtool_ops adp_eth_ethtool_ops = {
	.get_drvinfo		= adp_eth_get_drvinfo,
	.get_link		= ethtool_op_get_link,
	.get_link_ksettings	= phy_ethtool_get_link_ksettings,
	.set_link_ksettings	= phy_ethtool_set_link_ksettings,
	.nway_reset		= phy_ethtool_nway_reset,
	.get_sset_count		= adp_eth_get_sset_count,
	.get_strings		= adp_eth_get_strings,
	.get_ethtool_stats	= adp_eth_get_ethtool_stats,
	.get_ringparam		= adp_eth_get_ringparam,
	.get_regs_len		= adp_eth_get_regs_len,
	.get_regs		= adp_eth_get_regs,
	.get_ts_info		= adp_eth_get_ts_info,
};

static int adp_eth_probe(struct platform_device *pdev)
{
	struct net_device *netdev;
	struct adp_eth *priv;
	int irq, err;

	netdev = devm_alloc_etherdev(&pdev->dev, sizeof(*priv));
	if (!netdev)
		return -ENOMEM;
	SET_NETDEV_DEV(netdev, &pdev->dev);
	platform_set_drvdata(pdev, netdev);

	priv = netdev_priv(netdev);
	priv->netdev = netdev;
	priv->dev = &pdev->dev;

	netdev->tstats = devm_netdev_alloc_pcpu_stats(&pdev->dev, struct pcpu_sw_netstats);
	if (!netdev->tstats)
		return -ENOMEM;

	priv->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(priv->base))
		return PTR_ERR(priv->base);
	priv->has_ptp = resource_size(platform_get_resource(pdev, IORESOURCE_MEM, 0)) >= ETH_REGS_LEN_PTP;
	spin_lock_init(&priv->ptp_lock);
	spin_lock_init(&priv->ts_lock);
	hrtimer_setup(&priv->ts_timer, adp_eth_ts_timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL_SOFT);
	irq = platform_get_irq(pdev, 0);
	if (irq < 0)
		return irq;
	netdev->irq = irq;

	/* The engine has 32-bit addresses. */
	err = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
	if (err)
		return err;
	priv->hdr = dmam_alloc_coherent(&pdev->dev, TX_FRAMES * TX_HDR_SIZE, &priv->hdr_dma, GFP_KERNEL);
	if (!priv->hdr)
		return -ENOMEM;

	err = of_get_ethdev_address(pdev->dev.of_node, netdev);
	if (err)
		eth_hw_addr_random(netdev);

	if (priv->has_ptp) {
		priv->incr_nom = (u64)adp_rd(priv, ETH_PTP_INCR_NOM_HI) << 32 | adp_rd(priv, ETH_PTP_INCR_NOM);
		priv->ptp_info = adp_eth_ptp_info;
		priv->ptp = ptp_clock_register(&priv->ptp_info, &pdev->dev);
		if (IS_ERR(priv->ptp))
			return dev_err_probe(&pdev->dev, PTR_ERR(priv->ptp), "ptp_clock_register\n");
		/* The clock starts at the system time. */
		{
			struct timespec64 now;

			ktime_get_real_ts64(&now);
			adp_eth_ptp_settime(&priv->ptp_info, &now);
		}
		ptp_schedule_worker(priv->ptp, HZ);
		netdev->priv_flags |= IFF_UNICAST_FLT;
	}

	netdev->netdev_ops = &adp_eth_netdev_ops;
	netdev->ethtool_ops = &adp_eth_ethtool_ops;
	netdev->watchdog_timeo = HZ;
	netdev->max_mtu = MAX_FRAME - VLAN_ETH_HLEN;
	netdev->hw_features = NETIF_F_RXCSUM | NETIF_F_SG | NETIF_F_HW_CSUM | NETIF_F_TSO | NETIF_F_TSO6;
	netdev->features = netdev->hw_features;
	netif_set_tso_max_segs(netdev, TX_FRAMES / 2);
	netif_napi_add_weight(netdev, &priv->napi, adp_eth_poll, NAPI_WEIGHT);

	/*
	 * The PHY attaches under the RTNL lock, after the registration and before an open, so that
	 * the sysfs links of phylib exist.
	 */
	netif_carrier_off(netdev);
	rtnl_lock();
	err = register_netdevice(netdev);
	if (!err && platform_get_resource(pdev, IORESOURCE_MEM, 1)) {
		err = adp_eth_mdio_init(pdev, priv);
		if (err)
			unregister_netdevice(netdev);
	}
	rtnl_unlock();
	if (err) {
		if (priv->ptp)
			ptp_clock_unregister(priv->ptp);
		hrtimer_cancel(&priv->ts_timer);
		return dev_err_probe(&pdev->dev, err, "register\n");
	}
	netdev_info(netdev, "irq %d, %d receive buffers, %d frames in flight\n", irq, RX_BUFS, TX_FRAMES);
	return 0;
}

static void adp_eth_remove(struct platform_device *pdev)
{
	struct net_device *netdev = platform_get_drvdata(pdev);
	struct adp_eth *priv = netdev_priv(netdev);

	unregister_netdev(netdev);
	adp_eth_mdio_remove(priv);
	if (priv->ptp)
		ptp_clock_unregister(priv->ptp);
	hrtimer_cancel(&priv->ts_timer);
	dev_kfree_skb_any(priv->ts_skb);
}

static const struct of_device_id adp_eth_of_match[] = {
	{ .compatible = "adp-xc7k160,ethmac" },
	{ }
};
MODULE_DEVICE_TABLE(of, adp_eth_of_match);

static struct platform_driver adp_eth_driver = {
	.probe = adp_eth_probe,
	.remove = adp_eth_remove,
	.driver = {
		.name = "adp-ethmac",
		.of_match_table = adp_eth_of_match,
	},
};
module_platform_driver(adp_eth_driver);

MODULE_DESCRIPTION("Ethernet MAC with DMA of the ADP-XC7K160 LiteX SoC");
MODULE_LICENSE("Dual MIT/GPL");
