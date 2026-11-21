// SPDX-License-Identifier: GPL-2.0 OR MIT
/*
 * ALSA driver for the AC97 controller of the ADP-XC7K160 LiteX SoC (litex/adp_ac97.py) and its
 * ALC203 codec. One PCM device: playback and capture, 48 kHz, S16_LE, two channels. The DMA
 * engines of the SoC read and write the ring buffers in RAM, and raise an interrupt after each
 * period. snd-ac97-codec gives the mixer of the codec.
 *
 * The CSR offsets follow the order of the CSRs in adp_ac97.py. litex/linux/mkdts.py checks them
 * against csr.json when it makes the node.
 *
 * README section 15 of the ADP-XC7K160 board notes says how to build it.
 */

#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/interrupt.h>
#include <linux/iopoll.h>
#include <linux/litex.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <sound/ac97_codec.h>
#include <sound/core.h>
#include <sound/initval.h>
#include <sound/pcm.h>

#define AC97_CTRL		0x00	/* bit 0: hold the codec in reset */
#define AC97_CMD		0x04	/* addr 6:0, data 22:7, read 23 */
#define AC97_CMD_GO		0x08
#define AC97_STATUS		0x0c	/* busy 0, timeout 1, codec_ready 2 */
#define AC97_REPLY		0x10
#define AC97_PLAY		0x14	/* base (64 bits), length, enable, done, loop, offset, error */
#define AC97_REC		0x34
#define AC97_PLAY_PERIOD	0x54
#define AC97_REC_PERIOD		0x58
#define AC97_EV_STATUS		0x5c
#define AC97_EV_PENDING		0x60
#define AC97_EV_ENABLE		0x64

/* Offsets inside the block of a DMA engine. */
#define DMA_BASE		0x00
#define DMA_LENGTH		0x08
#define DMA_ENABLE		0x0c
#define DMA_LOOP		0x14
#define DMA_OFFSET		0x18

#define STATUS_BUSY		BIT(0)
#define STATUS_TIMEOUT		BIT(1)
#define STATUS_READY		BIT(2)

#define EV_PLAYBACK		BIT(0)
#define EV_CAPTURE		BIT(1)

struct adp_ac97 {
	struct device *dev;
	void __iomem *base;
	struct snd_card *card;
	struct snd_ac97 *ac97;
	struct snd_pcm *pcm;
	struct mutex cmd_lock;		/* one codec command at a time */
	spinlock_t lock;		/* the substreams and the event enable */
	struct snd_pcm_substream *substream[2];	/* SNDRV_PCM_STREAM_PLAYBACK, _CAPTURE */
};

static void __iomem *adp_ac97_dma(struct adp_ac97 *priv, int stream)
{
	return priv->base + (stream == SNDRV_PCM_STREAM_PLAYBACK ? AC97_PLAY : AC97_REC);
}

/* Codec registers ------------------------------------------------------------------------------ */

static int adp_ac97_command(struct adp_ac97 *priv, unsigned short reg, unsigned short data,
			    bool read, unsigned short *reply)
{
	u32 status;
	int ret;

	mutex_lock(&priv->cmd_lock);
	litex_write32(priv->base + AC97_CMD, (reg & 0x7f) | ((u32)data << 7) | ((u32)read << 23));
	litex_write32(priv->base + AC97_CMD_GO, 1);
	/* A write takes one frame (20.8 us); a read waits for the reply in a later frame. */
	ret = readx_poll_timeout(litex_read32, priv->base + AC97_STATUS, status,
				 !(status & STATUS_BUSY), 20, 5000);
	if (!ret && (status & STATUS_TIMEOUT))
		ret = -EIO;
	if (!ret && reply)
		*reply = litex_read32(priv->base + AC97_REPLY);
	mutex_unlock(&priv->cmd_lock);
	return ret;
}

static unsigned short adp_ac97_read(struct snd_ac97 *ac97, unsigned short reg)
{
	struct adp_ac97 *priv = ac97->private_data;
	unsigned short value = 0xffff;

	if (adp_ac97_command(priv, reg, 0, true, &value))
		dev_dbg(priv->dev, "no reply for register 0x%02x\n", reg);
	return value;
}

static void adp_ac97_write(struct snd_ac97 *ac97, unsigned short reg, unsigned short val)
{
	struct adp_ac97 *priv = ac97->private_data;

	if (adp_ac97_command(priv, reg, val, false, NULL))
		dev_dbg(priv->dev, "write of register 0x%02x failed\n", reg);
}

/* A cold reset: AC97_RSTn low, then high. The controller keeps SYNC and SDATA_OUT low meanwhile. */
static int adp_ac97_cold_reset(struct adp_ac97 *priv)
{
	u32 status;

	litex_write32(priv->base + AC97_CTRL, 1);
	usleep_range(10, 20);
	litex_write32(priv->base + AC97_CTRL, 0);
	return readx_poll_timeout(litex_read32, priv->base + AC97_STATUS, status,
				  status & STATUS_READY, 1000, 1000000);
}

static void adp_ac97_reset(struct snd_ac97 *ac97)
{
	struct adp_ac97 *priv = ac97->private_data;

	if (adp_ac97_cold_reset(priv))
		dev_err(priv->dev, "the codec is not ready after a reset\n");
}

static const struct snd_ac97_bus_ops adp_ac97_bus_ops = {
	.reset = adp_ac97_reset,
	.read  = adp_ac97_read,
	.write = adp_ac97_write,
};

/* PCM ------------------------------------------------------------------------------------------ */

static const struct snd_pcm_hardware adp_ac97_hw = {
	.info = SNDRV_PCM_INFO_MMAP | SNDRV_PCM_INFO_MMAP_VALID | SNDRV_PCM_INFO_INTERLEAVED |
		SNDRV_PCM_INFO_BLOCK_TRANSFER,
	.formats = SNDRV_PCM_FMTBIT_S16_LE,
	.rates = SNDRV_PCM_RATE_48000,
	.rate_min = 48000,
	.rate_max = 48000,
	.channels_min = 2,
	.channels_max = 2,
	.buffer_bytes_max = 256 * 1024,
	.period_bytes_min = 256,
	.period_bytes_max = 64 * 1024,
	.periods_min = 2,
	.periods_max = 256,
};

static void adp_ac97_set_event(struct adp_ac97 *priv, int stream, bool on)
{
	u32 bit = stream == SNDRV_PCM_STREAM_PLAYBACK ? EV_PLAYBACK : EV_CAPTURE;
	unsigned long flags;
	u32 enable;

	spin_lock_irqsave(&priv->lock, flags);
	enable = litex_read32(priv->base + AC97_EV_ENABLE);
	enable = on ? enable | bit : enable & ~bit;
	litex_write32(priv->base + AC97_EV_PENDING, bit);
	litex_write32(priv->base + AC97_EV_ENABLE, enable);
	spin_unlock_irqrestore(&priv->lock, flags);
}

static int adp_ac97_pcm_open(struct snd_pcm_substream *substream)
{
	struct adp_ac97 *priv = snd_pcm_substream_chip(substream);
	struct snd_pcm_runtime *runtime = substream->runtime;
	unsigned long flags;
	int ret;

	runtime->hw = adp_ac97_hw;
	/* The interrupt comes after each period counted from the start of the buffer. */
	ret = snd_pcm_hw_constraint_integer(runtime, SNDRV_PCM_HW_PARAM_PERIODS);
	if (ret < 0)
		return ret;
	spin_lock_irqsave(&priv->lock, flags);
	priv->substream[substream->stream] = substream;
	spin_unlock_irqrestore(&priv->lock, flags);
	return 0;
}

static int adp_ac97_pcm_close(struct snd_pcm_substream *substream)
{
	struct adp_ac97 *priv = snd_pcm_substream_chip(substream);
	unsigned long flags;

	adp_ac97_set_event(priv, substream->stream, false);
	spin_lock_irqsave(&priv->lock, flags);
	priv->substream[substream->stream] = NULL;
	spin_unlock_irqrestore(&priv->lock, flags);
	return 0;
}

static int adp_ac97_pcm_prepare(struct snd_pcm_substream *substream)
{
	struct adp_ac97 *priv = snd_pcm_substream_chip(substream);
	struct snd_pcm_runtime *runtime = substream->runtime;
	void __iomem *dma = adp_ac97_dma(priv, substream->stream);

	litex_write32(dma + DMA_ENABLE, 0);
	litex_write64(dma + DMA_BASE, runtime->dma_addr);
	litex_write32(dma + DMA_LENGTH, snd_pcm_lib_buffer_bytes(substream));
	litex_write32(dma + DMA_LOOP, 1);
	/* One word of the DMA is one stereo frame of S16_LE. */
	litex_write32(priv->base + (substream->stream == SNDRV_PCM_STREAM_PLAYBACK ?
				    AC97_PLAY_PERIOD : AC97_REC_PERIOD), runtime->period_size);
	adp_ac97_set_event(priv, substream->stream, true);
	return 0;
}

static int adp_ac97_pcm_trigger(struct snd_pcm_substream *substream, int cmd)
{
	struct adp_ac97 *priv = snd_pcm_substream_chip(substream);
	void __iomem *dma = adp_ac97_dma(priv, substream->stream);

	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_RESUME:
		litex_write32(dma + DMA_ENABLE, 1);
		return 0;
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_SUSPEND:
		litex_write32(dma + DMA_ENABLE, 0);
		return 0;
	default:
		return -EINVAL;
	}
}

static snd_pcm_uframes_t adp_ac97_pcm_pointer(struct snd_pcm_substream *substream)
{
	struct adp_ac97 *priv = snd_pcm_substream_chip(substream);
	u32 words = litex_read32(adp_ac97_dma(priv, substream->stream) + DMA_OFFSET);

	return words % substream->runtime->buffer_size;
}

static const struct snd_pcm_ops adp_ac97_pcm_ops = {
	.open    = adp_ac97_pcm_open,
	.close   = adp_ac97_pcm_close,
	.prepare = adp_ac97_pcm_prepare,
	.trigger = adp_ac97_pcm_trigger,
	.pointer = adp_ac97_pcm_pointer,
};

static irqreturn_t adp_ac97_irq(int irq, void *data)
{
	struct adp_ac97 *priv = data;
	struct snd_pcm_substream *play, *rec;
	u32 pending;

	pending = litex_read32(priv->base + AC97_EV_PENDING);
	if (!pending)
		return IRQ_NONE;
	litex_write32(priv->base + AC97_EV_PENDING, pending);

	spin_lock(&priv->lock);
	play = priv->substream[SNDRV_PCM_STREAM_PLAYBACK];
	rec = priv->substream[SNDRV_PCM_STREAM_CAPTURE];
	spin_unlock(&priv->lock);
	if ((pending & EV_PLAYBACK) && play)
		snd_pcm_period_elapsed(play);
	if ((pending & EV_CAPTURE) && rec)
		snd_pcm_period_elapsed(rec);
	return IRQ_HANDLED;
}

/* Probe ---------------------------------------------------------------------------------------- */

static void adp_ac97_hold_reset(void *data)
{
	struct adp_ac97 *priv = data;

	litex_write32(priv->base + AC97_PLAY + DMA_ENABLE, 0);
	litex_write32(priv->base + AC97_REC + DMA_ENABLE, 0);
	litex_write32(priv->base + AC97_EV_ENABLE, 0);
	litex_write32(priv->base + AC97_CTRL, 1);
}

static int adp_ac97_probe(struct platform_device *pdev)
{
	static const struct snd_ac97_template template_zero;
	struct snd_ac97_template template = template_zero;
	struct device *dev = &pdev->dev;
	struct snd_ac97_bus *bus;
	struct adp_ac97 *priv;
	struct snd_card *card;
	int irq, ret;

	ret = snd_devm_card_new(dev, SNDRV_DEFAULT_IDX1, SNDRV_DEFAULT_STR1, THIS_MODULE,
				sizeof(*priv), &card);
	if (ret)
		return ret;
	priv = card->private_data;
	priv->dev = dev;
	priv->card = card;
	mutex_init(&priv->cmd_lock);
	spin_lock_init(&priv->lock);

	priv->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(priv->base))
		return PTR_ERR(priv->base);
	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));
	if (ret)
		return ret;

	adp_ac97_hold_reset(priv);
	ret = devm_add_action(dev, adp_ac97_hold_reset, priv);
	if (ret)
		return ret;
	if (adp_ac97_cold_reset(priv))
		return dev_err_probe(dev, -ENODEV, "the codec did not become ready\n");

	irq = platform_get_irq(pdev, 0);
	if (irq < 0)
		return irq;
	ret = devm_request_irq(dev, irq, adp_ac97_irq, 0, dev_name(dev), priv);
	if (ret)
		return ret;

	ret = snd_ac97_bus(card, 0, &adp_ac97_bus_ops, NULL, &bus);
	if (ret)
		return ret;
	template.private_data = priv;
	ret = snd_ac97_mixer(bus, &template, &priv->ac97);
	if (ret)
		return dev_err_probe(dev, ret, "no AC97 mixer\n");

	ret = snd_pcm_new(card, "AC97", 0, 1, 1, &priv->pcm);
	if (ret)
		return ret;
	priv->pcm->private_data = priv;
	strscpy(priv->pcm->name, "ADP-XC7K160 AC97", sizeof(priv->pcm->name));
	snd_pcm_set_ops(priv->pcm, SNDRV_PCM_STREAM_PLAYBACK, &adp_ac97_pcm_ops);
	snd_pcm_set_ops(priv->pcm, SNDRV_PCM_STREAM_CAPTURE, &adp_ac97_pcm_ops);
	snd_pcm_set_managed_buffer_all(priv->pcm, SNDRV_DMA_TYPE_DEV, dev, 64 * 1024,
				       adp_ac97_hw.buffer_bytes_max);

	strscpy(card->driver, "adp-ac97", sizeof(card->driver));
	strscpy(card->shortname, "ADP-XC7K160 AC97", sizeof(card->shortname));
	snprintf(card->longname, sizeof(card->longname), "%s (%s) at %pa irq %d",
		 card->shortname, snd_ac97_get_short_name(priv->ac97),
		 &pdev->resource[0].start, irq);
	return snd_card_register(card);
}

static const struct of_device_id adp_ac97_of_match[] = {
	{ .compatible = "adp-xc7k160,ac97" },
	{ }
};
MODULE_DEVICE_TABLE(of, adp_ac97_of_match);

static struct platform_driver adp_ac97_driver = {
	.driver = {
		.name = "adp-ac97",
		.of_match_table = adp_ac97_of_match,
	},
	.probe = adp_ac97_probe,
};
module_platform_driver(adp_ac97_driver);

MODULE_DESCRIPTION("AC97 controller of the ADP-XC7K160 LiteX SoC");
MODULE_LICENSE("Dual MIT/GPL");
