// SPDX-License-Identifier: GPL-2.0 OR MIT
/*
 * hwmon driver for the XADC of a LiteX SoC (S7SystemMonitor of litex/soc/cores/xadc.py).
 *
 * The SoC samples the temperature, VCCINT, VCCAUX and VCCBRAM all the time and keeps the last
 * raw 12-bit value of each in a CSR. litex_json2dts_linux describes them in a node with
 * compatible "litex,hwmon-xadc": the CSR offsets, and the scale of each value. This driver only
 * reads those CSRs; it does not write the XADC.
 *
 *   temperature (millidegree Celsius) = raw * temperature-mul / temperature-div - temperature-offset
 *   voltage (millivolt)               = raw * voltage-mul / voltage-div
 *
 * README section 15 of the ADP-XC7K160 board notes says how to build it.
 */

#include <linux/err.h>
#include <linux/hwmon.h>
#include <linux/litex.h>
#include <linux/math64.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/property.h>

enum { XADC_TEMP, XADC_VCCINT, XADC_VCCAUX, XADC_VCCBRAM, XADC_COUNT };

static const char * const xadc_offset_props[XADC_COUNT] = {
	"litex,temperature-csr-offset",
	"litex,vccint-csr-offset",
	"litex,vccaux-csr-offset",
	"litex,vccbram-csr-offset",
};

static const char * const xadc_in_labels[] = { "vccint", "vccaux", "vccbram" };

struct litex_xadc {
	void __iomem *base;
	u32 offset[XADC_COUNT];
	u32 temp_mul, temp_div, temp_offset;
	u32 volt_mul, volt_div;
};

static u32 litex_xadc_raw(struct litex_xadc *xadc, int index)
{
	return litex_read32(xadc->base + xadc->offset[index]) & 0xfff;
}

static umode_t litex_xadc_is_visible(const void *data, enum hwmon_sensor_types type,
				     u32 attr, int channel)
{
	return 0444;
}

static int litex_xadc_read(struct device *dev, enum hwmon_sensor_types type, u32 attr,
			   int channel, long *val)
{
	struct litex_xadc *xadc = dev_get_drvdata(dev);
	s64 raw;

	if (type == hwmon_temp && attr == hwmon_temp_input) {
		raw = litex_xadc_raw(xadc, XADC_TEMP);
		*val = div_s64(raw * xadc->temp_mul, xadc->temp_div) - xadc->temp_offset;
		return 0;
	}
	if (type == hwmon_in && attr == hwmon_in_input && channel < 3) {
		raw = litex_xadc_raw(xadc, XADC_VCCINT + channel);
		*val = div_s64(raw * xadc->volt_mul, xadc->volt_div);
		return 0;
	}
	return -EOPNOTSUPP;
}

static int litex_xadc_read_string(struct device *dev, enum hwmon_sensor_types type, u32 attr,
				  int channel, const char **str)
{
	if (type == hwmon_temp && attr == hwmon_temp_label) {
		*str = "fpga";
		return 0;
	}
	if (type == hwmon_in && attr == hwmon_in_label && channel < 3) {
		*str = xadc_in_labels[channel];
		return 0;
	}
	return -EOPNOTSUPP;
}

static const struct hwmon_ops litex_xadc_ops = {
	.is_visible = litex_xadc_is_visible,
	.read = litex_xadc_read,
	.read_string = litex_xadc_read_string,
};

static const struct hwmon_channel_info * const litex_xadc_info[] = {
	HWMON_CHANNEL_INFO(temp, HWMON_T_INPUT | HWMON_T_LABEL),
	HWMON_CHANNEL_INFO(in,
			   HWMON_I_INPUT | HWMON_I_LABEL,
			   HWMON_I_INPUT | HWMON_I_LABEL,
			   HWMON_I_INPUT | HWMON_I_LABEL),
	NULL
};

static const struct hwmon_chip_info litex_xadc_chip_info = {
	.ops = &litex_xadc_ops,
	.info = litex_xadc_info,
};

static int litex_xadc_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct litex_xadc *xadc;
	struct device *hwmon;
	int i, ret = 0;

	xadc = devm_kzalloc(dev, sizeof(*xadc), GFP_KERNEL);
	if (!xadc)
		return -ENOMEM;

	xadc->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(xadc->base))
		return PTR_ERR(xadc->base);

	for (i = 0; i < XADC_COUNT; i++)
		ret |= device_property_read_u32(dev, xadc_offset_props[i], &xadc->offset[i]);
	ret |= device_property_read_u32(dev, "litex,temperature-mul", &xadc->temp_mul);
	ret |= device_property_read_u32(dev, "litex,temperature-div", &xadc->temp_div);
	ret |= device_property_read_u32(dev, "litex,temperature-offset", &xadc->temp_offset);
	ret |= device_property_read_u32(dev, "litex,voltage-mul", &xadc->volt_mul);
	ret |= device_property_read_u32(dev, "litex,voltage-div", &xadc->volt_div);
	if (ret || !xadc->temp_div || !xadc->volt_div)
		return dev_err_probe(dev, -EINVAL, "missing or invalid litex,* properties\n");

	hwmon = devm_hwmon_device_register_with_info(dev, "litex_xadc", xadc,
						     &litex_xadc_chip_info, NULL);
	return PTR_ERR_OR_ZERO(hwmon);
}

static const struct of_device_id litex_xadc_of_match[] = {
	{ .compatible = "litex,hwmon-xadc" },
	{ }
};
MODULE_DEVICE_TABLE(of, litex_xadc_of_match);

static struct platform_driver litex_xadc_driver = {
	.driver = {
		.name = "litex-xadc-hwmon",
		.of_match_table = litex_xadc_of_match,
	},
	.probe = litex_xadc_probe,
};
module_platform_driver(litex_xadc_driver);

MODULE_DESCRIPTION("hwmon driver for the XADC of a LiteX SoC");
MODULE_LICENSE("Dual MIT/GPL");
