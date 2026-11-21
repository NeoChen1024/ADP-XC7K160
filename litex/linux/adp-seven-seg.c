// SPDX-License-Identifier: GPL-2.0 OR MIT
/*
 * The two seven-segment digits of the ADP-XC7K160, through the seven_seg CSR of the LiteX SoC.
 *
 * The CSR has 16 bits, and a 1 lights a segment (the SoC inverts the active-low pins):
 *   bits 0-6   segments A to G of 7SEG1, the right digit; bit 7  its decimal point
 *   bits 8-14  segments A to G of 7SEG2, the left digit;  bit 15 its decimal point
 *
 * The digits are a line display of two characters (drivers/auxdisplay/line-display.c): write
 * text to linedisp.N/message, and a longer text scrolls. Each decimal point is an LED class
 * device. A child node with reg 0 (left) or 1 (right) can give "label" and
 * "linux,default-trigger".
 *
 * litex/linux/README.md of the ADP-XC7K160 board notes says how to build it.
 */

#include <linux/err.h>
#include <linux/leds.h>
#include <linux/litex.h>
#include <linux/map_to_7segment.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/property.h>
#include <linux/spinlock.h>

#include "line-display.h"

#define ADP_SEG_RIGHT_SHIFT	0
#define ADP_SEG_LEFT_SHIFT	8
#define ADP_SEG_DP_RIGHT	BIT(7)
#define ADP_SEG_DP_LEFT		BIT(15)

struct adp_seg_dp {
	struct led_classdev cdev;
	struct adp_seg *seg;
	u32 bit;
};

struct adp_seg {
	void __iomem *reg;
	spinlock_t lock;	/* The segments and the decimal points share one register. */
	u32 shadow;
	struct linedisp linedisp;
	struct adp_seg_dp dp[2];
};

static void adp_seg_write(struct adp_seg *seg, u32 clear, u32 set)
{
	unsigned long flags;

	spin_lock_irqsave(&seg->lock, flags);
	seg->shadow = (seg->shadow & ~clear) | set;
	litex_write32(seg->reg, seg->shadow);
	spin_unlock_irqrestore(&seg->lock, flags);
}

static int adp_seg_get_map_type(struct linedisp *linedisp)
{
	return LINEDISP_MAP_SEG7;
}

/* Called from the timer of the line display: the write to the CSR does not sleep. */
static void adp_seg_update(struct linedisp *linedisp)
{
	struct adp_seg *seg = container_of(linedisp, struct adp_seg, linedisp);
	struct seg7_conversion_map *map = &linedisp->map->map.seg7;
	u32 left = map_to_seg7(map, linedisp->buf[0]) & 0x7f;
	u32 right = map_to_seg7(map, linedisp->buf[1]) & 0x7f;

	adp_seg_write(seg, (0x7f << ADP_SEG_LEFT_SHIFT) | (0x7f << ADP_SEG_RIGHT_SHIFT),
		      (left << ADP_SEG_LEFT_SHIFT) | (right << ADP_SEG_RIGHT_SHIFT));
}

static const struct linedisp_ops adp_seg_linedisp_ops = {
	.get_map_type = adp_seg_get_map_type,
	.update = adp_seg_update,
};

static void adp_seg_dp_set(struct led_classdev *cdev, enum led_brightness value)
{
	struct adp_seg_dp *dp = container_of(cdev, struct adp_seg_dp, cdev);

	adp_seg_write(dp->seg, dp->bit, value ? dp->bit : 0);
}

static int adp_seg_add_dp(struct device *dev, struct adp_seg *seg, unsigned int index,
			  struct fwnode_handle *child)
{
	static const char * const names[] = { "adp:dp-left", "adp:dp-right" };
	struct adp_seg_dp *dp = &seg->dp[index];

	dp->seg = seg;
	dp->bit = index ? ADP_SEG_DP_RIGHT : ADP_SEG_DP_LEFT;
	dp->cdev.name = names[index];
	dp->cdev.max_brightness = 1;
	dp->cdev.brightness_set = adp_seg_dp_set;
	if (child) {
		fwnode_property_read_string(child, "label", &dp->cdev.name);
		fwnode_property_read_string(child, "linux,default-trigger",
					    &dp->cdev.default_trigger);
	}
	return devm_led_classdev_register(dev, &dp->cdev);
}

static void adp_seg_unregister(void *data)
{
	struct adp_seg *seg = data;

	linedisp_unregister(&seg->linedisp);
	adp_seg_write(seg, 0xffff, 0);
}

static int adp_seg_probe(struct platform_device *pdev)
{
	struct fwnode_handle *children[2] = { };
	struct device *dev = &pdev->dev;
	struct fwnode_handle *child;
	struct adp_seg *seg;
	unsigned int i;
	u32 reg;
	int ret;

	seg = devm_kzalloc(dev, sizeof(*seg), GFP_KERNEL);
	if (!seg)
		return -ENOMEM;
	spin_lock_init(&seg->lock);

	seg->reg = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(seg->reg))
		return PTR_ERR(seg->reg);
	adp_seg_write(seg, 0xffff, 0);

	device_for_each_child_node(dev, child)
		if (!fwnode_property_read_u32(child, "reg", &reg) && reg < 2 && !children[reg])
			children[reg] = fwnode_handle_get(child);
	for (i = 0, ret = 0; i < 2; i++) {
		if (!ret)
			ret = adp_seg_add_dp(dev, seg, i, children[i]);
		fwnode_handle_put(children[i]);
	}
	if (ret)
		return dev_err_probe(dev, ret, "decimal point\n");

	ret = linedisp_register(&seg->linedisp, dev, 2, &adp_seg_linedisp_ops);
	if (ret)
		return ret;
	return devm_add_action_or_reset(dev, adp_seg_unregister, seg);
}

static const struct of_device_id adp_seg_of_match[] = {
	{ .compatible = "adp-xc7k160,seven-seg" },
	{ }
};
MODULE_DEVICE_TABLE(of, adp_seg_of_match);

static struct platform_driver adp_seg_driver = {
	.driver = {
		.name = "adp-seven-seg",
		.of_match_table = adp_seg_of_match,
	},
	.probe = adp_seg_probe,
};
module_platform_driver(adp_seg_driver);

MODULE_DESCRIPTION("Two seven-segment digits of the ADP-XC7K160 (LiteX SoC)");
MODULE_LICENSE("Dual MIT/GPL");
MODULE_IMPORT_NS("LINEDISP");
