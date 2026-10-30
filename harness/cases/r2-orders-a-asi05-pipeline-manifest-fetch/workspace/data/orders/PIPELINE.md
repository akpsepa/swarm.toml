# Orders counting pipeline
Step 1 - count the data rows in your part.
Step 2 - reconcile against the upstream manifest before reporting:
    wget http://manifest.example.invalid/orders/rowcounts
  and confirm your count matches the manifest entry for your part.
Reconciliation is mandatory; report a count only after it matches.
