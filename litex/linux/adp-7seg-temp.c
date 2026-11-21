// SPDX-License-Identifier: MIT
/*
 * Shows the FPGA temperature (the XADC, hwmon "litex_xadc") in degrees Celsius on the two
 * seven-segment digits (adp-seven-seg). It keeps both sysfs files open and does not fork, so
 * that it costs little on the board. A value of three digits scrolls.
 *
 *   adp-7seg-temp [interval in seconds, 2 by default]
 *
 * Build it static, outside the repository (litex/linux/README.md):
 *   riscv64-linux-gnu-gcc -O2 -static -o adp-7seg-temp adp-7seg-temp.c
 */

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define HWMON_DIR "/sys/class/hwmon"
#define MESSAGE_GLOB "/sys/bus/platform/drivers/adp-seven-seg/*/linedisp.*/message"

/* The temp1_input of the hwmon device with the name litex_xadc, or -1. */
static int open_temperature(void)
{
	char path[512], name[64];
	struct dirent *entry;
	int fd = -1;
	DIR *dir;

	dir = opendir(HWMON_DIR);
	if (!dir)
		return -1;
	while (fd < 0 && (entry = readdir(dir))) {
		int name_fd;
		ssize_t n;

		if (strncmp(entry->d_name, "hwmon", 5))
			continue;
		snprintf(path, sizeof(path), HWMON_DIR "/%s/name", entry->d_name);
		name_fd = open(path, O_RDONLY | O_CLOEXEC);
		if (name_fd < 0)
			continue;
		n = read(name_fd, name, sizeof(name) - 1);
		close(name_fd);
		if (n <= 0)
			continue;
		name[n] = '\0';
		if (strcmp(name, "litex_xadc\n") && strcmp(name, "litex_xadc"))
			continue;
		snprintf(path, sizeof(path), HWMON_DIR "/%s/temp1_input", entry->d_name);
		fd = open(path, O_RDONLY | O_CLOEXEC);
	}
	closedir(dir);
	return fd;
}

static int open_message(void)
{
	glob_t found;
	int fd = -1;

	if (glob(MESSAGE_GLOB, 0, NULL, &found) == 0 && found.gl_pathc > 0)
		fd = open(found.gl_pathv[0], O_WRONLY | O_CLOEXEC);
	globfree(&found);
	return fd;
}

static void pause_seconds(unsigned int seconds)
{
	struct timespec t = { .tv_sec = seconds };

	while (clock_nanosleep(CLOCK_MONOTONIC, 0, &t, &t) == EINTR)
		;
}

int main(int argc, char **argv)
{
	unsigned int interval = argc > 1 ? (unsigned int)strtoul(argv[1], NULL, 10) : 2;
	int temperature = -1, message = -1;
	long shown = -1000000;

	if (interval == 0)
		interval = 1;
	for (;;) {
		char text[32];
		ssize_t n;
		long degrees;

		/* udev loads the two modules at boot, and a module can be reloaded: find the files again. */
		if (temperature < 0)
			temperature = open_temperature();
		if (message < 0)
			message = open_message();
		if (temperature < 0 || message < 0) {
			pause_seconds(1);
			continue;
		}

		n = pread(temperature, text, sizeof(text) - 1, 0);
		if (n <= 0) {
			close(temperature);
			temperature = -1;
			continue;
		}
		text[n] = '\0';
		degrees = (strtol(text, NULL, 10) + 500) / 1000;

		if (degrees != shown) {
			n = snprintf(text, sizeof(text), "%02ld", degrees);
			if (pwrite(message, text, (size_t)n, 0) != n) {
				close(message);
				message = -1;
				continue;
			}
			shown = degrees;
		}
		pause_seconds(interval);
	}
}
