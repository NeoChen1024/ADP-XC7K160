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
 * The CSR offsets follow the order of the CSRs in adp_ethmac.py. litex/linux/mkdts.py checks
 * them against csr.json when it makes the node.
 *
 * litex/linux/README.md of the ADP-XC7K160 board notes says how to build it.
 */

#include <linux/dma-mapping.h>
#include <linux/etherdevice.h>
#include <linux/if_vlan.h>
#include <linux/interrupt.h>
#include <linux/litex.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/of_net.h>
#include <linux/platform_device.h>
#include <linux/skbuff.h>
#include <linux/tcp.h>
#include <linux/udp.h>
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
#define ETH_CRC_ERRORS		0x3c

#define RX_STAT_READY		BIT(31)
#define RX_STAT_ERROR		BIT(16)
#define RX_STAT_LENGTH		GENMASK(10, 0)

#define TX_CTRL_LAST		BIT(11)
#define TX_CTRL_CSUM		BIT(12)
#define TX_CTRL_UDP		BIT(13)
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

struct adp_eth {
	void __iomem *base;
	struct net_device *netdev;
	struct device *dev;
	struct napi_struct napi;

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

	priv->tx_skb = skb;
	if (!adp_eth_tx_fill(priv) || !adp_eth_tx_room(priv)) {
		/* The poll continues a stopped queue, after the event of a sent frame. */
		netif_stop_queue(netdev);
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

	adp_wr(priv, ETH_EV_PENDING, EV_TX);
	err = request_irq(netdev->irq, adp_eth_interrupt, 0, netdev->name, netdev);
	if (err)
		return err;
	napi_enable(&priv->napi);
	adp_wr(priv, ETH_EV_ENABLE, EV_RX | (priv->tx_skb_count ? EV_TX : 0));
	netif_carrier_on(netdev);
	netif_start_queue(netdev);
	return 0;
}

static int adp_eth_stop(struct net_device *netdev)
{
	struct adp_eth *priv = netdev_priv(netdev);

	netif_stop_queue(netdev);
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
	napi_schedule(&priv->napi);
}

static const struct net_device_ops adp_eth_netdev_ops = {
	.ndo_open		= adp_eth_open,
	.ndo_stop		= adp_eth_stop,
	.ndo_start_xmit		= adp_eth_start_xmit,
	.ndo_get_stats64	= adp_eth_get_stats64,
	.ndo_tx_timeout		= adp_eth_tx_timeout,
	.ndo_set_mac_address	= eth_mac_addr,
	.ndo_validate_addr	= eth_validate_addr,
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

	netdev->netdev_ops = &adp_eth_netdev_ops;
	netdev->watchdog_timeo = HZ;
	netdev->max_mtu = MAX_FRAME - VLAN_ETH_HLEN;
	netdev->hw_features = NETIF_F_RXCSUM | NETIF_F_SG | NETIF_F_HW_CSUM | NETIF_F_TSO | NETIF_F_TSO6;
	netdev->features = netdev->hw_features;
	netif_set_tso_max_segs(netdev, TX_FRAMES / 2);
	netif_napi_add_weight(netdev, &priv->napi, adp_eth_poll, NAPI_WEIGHT);

	err = register_netdev(netdev);
	if (err)
		return dev_err_probe(&pdev->dev, err, "register_netdev\n");
	netdev_info(netdev, "irq %d, %d receive buffers, %d frames in flight\n", irq, RX_BUFS, TX_FRAMES);
	return 0;
}

static void adp_eth_remove(struct platform_device *pdev)
{
	struct net_device *netdev = platform_get_drvdata(pdev);

	unregister_netdev(netdev);
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
