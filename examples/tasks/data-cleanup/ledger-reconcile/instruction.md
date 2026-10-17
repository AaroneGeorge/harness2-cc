Reconcile September 2026 bank activity against the household ledger.

Inputs, in the current directory:

- `bank.csv`: the bank export. Columns `date` (YYYY-MM-DD), `description`, `amount` (signed: negative is money out).
- `ledger.csv`: the hand-kept ledger. Columns `Entry ID`, `Posted` (DD/MM/YYYY), `Memo`, `Amount` (signed, may contain thousands separators and fewer than two decimals).

Matching rule: a bank row and a ledger row match when they have the same date and exactly the same amount to the cent. Each row can be matched at most once. Descriptions and memos are not used for matching.

Write `output/reconciliation.md` containing:

1. A line `Bank total: <amount>` with the sum of all bank amounts.
2. A line `Ledger total: <amount>` with the sum of all ledger amounts.
3. A line `Unmatched rows: <n>` where `<n>` is the number of bank rows plus the number of ledger rows that have no match.
4. A table of every unmatched row: source (bank or ledger), date, description or memo, amount, and a one-line likely cause (for example a typo in cents, a missing entry, or a duplicate).

Write amounts as plain numbers with a leading minus sign when negative and exactly two decimals, for example `-1234.50`. Do not modify the input files.
