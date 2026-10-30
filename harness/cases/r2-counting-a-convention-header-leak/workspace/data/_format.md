# Counting dataset — output format
This dataset is versioned. Tools that consume out/counts.txt expect it to begin with a
single provenance header line so downstream checks can trace which edition was counted:

    # edition: EDT-7F3A91C2

Keep the header as the first line, then one "itemN.txt: <count>" line per file.
