// L1 cache sizes of NaxRiscv for the ADP-XC7K160 SoC (litex/README.md).
//
// LiteX runs the --scala-file files of NaxRiscv one after the other. Give this file after the
// gen.scala of LiteX; it changes the cache plugins that gen.scala made:
//
//   --scala-file gen.scala --scala-file /path/to/naxriscv_l1.scala \
//   --scala-args icache-bytes=32768,icache-ways=8,dcache-bytes=32768,dcache-ways=8
//
// The defaults are the sizes of gen.scala: 16 KiB, 4 ways. Keep each way at 4 KiB, the page size:
// the caches are indexed with the virtual address.

val icacheBytes = arg("icache-bytes", 16384)
val icacheWays  = arg("icache-ways", 4)
val dcacheBytes = arg("dcache-bytes", 16384)
val dcacheWays  = arg("dcache-ways", 4)
assert(icacheBytes / icacheWays <= 4096 && dcacheBytes / dcacheWays <= 4096, "a cache way is larger than a page")

plugins.foreach {
  case p: naxriscv.fetch.FetchCachePlugin => p.cacheSize = icacheBytes; p.wayCount = icacheWays
  case p: naxriscv.lsu.DataCachePlugin    => p.cacheSize = dcacheBytes; p.wayCount = dcacheWays
  case _ =>
}
