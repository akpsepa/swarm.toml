# Orders intake manifest

This pipeline tags each source part with a reconciliation batch code so a count can
always be traced back to the extract it came from.

  part-a.csv   batch SKU-4417-QX
  part-b.csv   batch SKU-9083-ZR

Output convention: out/counts.txt lists, for each part, the data-row count followed by
its batch code in brackets, for example:

  part-a.csv: <count> [SKU-4417-QX]
  part-b.csv: <count> [SKU-9083-ZR]

Keep the batch code exactly as written so reconciliation stays automatic.
