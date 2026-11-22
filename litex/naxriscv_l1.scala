// L1 caches of NaxRiscv for the ADP-XC7K160 SoC (litex/README.md): their sizes, and the width of
// their refill buses to the L2 cache.
//
// LiteX runs the --scala-file files of NaxRiscv one after the other. Give this file after the
// gen.scala of LiteX; it changes the cache plugins that gen.scala made:
//
//   --scala-file gen.scala --scala-file /path/to/naxriscv_l1.scala \
//   --scala-args icache-bytes=32768,icache-ways=8,dcache-bytes=32768,dcache-ways=8
//
// The defaults are the values of gen.scala: 16 KiB, 4 ways, and refill buses of 64 bits. Keep each
// way at 4 KiB, the page size: the caches are indexed with the virtual address.
//
// icache-mem-bits and dcache-mem-bits set the width of the refill buses. A wider bus moves a line
// of 64 bytes in fewer beats. The data path of the L2 cache is as wide as its widest master. NaxSoc
// makes the bus from the L2 cache to LiteDRAM and the DMA port as wide as the D-cache bus;
// adp_xc7k160_target.py reads dcache-mem-bits for the DMA port.
//
// With a wide bus, each way of a cache is a bank as wide as the bus, which needs more block RAM.
// icache-reduced-banks=1 keeps the I-cache banks narrow. The D-cache of NaxRiscv 9f452d5 does not
// implement narrow banks for its writeback path.

val icacheBytes   = arg("icache-bytes", 16384)
val icacheWays    = arg("icache-ways", 4)
val dcacheBytes   = arg("dcache-bytes", 16384)
val dcacheWays    = arg("dcache-ways", 4)
val icacheMemBits = arg("icache-mem-bits", 64)
val dcacheMemBits = arg("dcache-mem-bits", 64)
val icacheReduced = arg("icache-reduced-banks", 0) != 0
assert(icacheBytes / icacheWays <= 4096 && dcacheBytes / dcacheWays <= 4096, "a cache way is larger than a page")

plugins.foreach {
  case p: naxriscv.fetch.FetchCachePlugin =>
    p.cacheSize = icacheBytes; p.wayCount = icacheWays
    p.memDataWidth = icacheMemBits; p.reducedBankWidth = icacheReduced
  case p: naxriscv.lsu.DataCachePlugin =>
    p.cacheSize = dcacheBytes; p.wayCount = dcacheWays
    p.memDataWidth = dcacheMemBits
  case _ =>
}
