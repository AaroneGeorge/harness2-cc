Deduplicate the contact list in `contacts.csv` (columns `name,email,phone,company`).

Two rows are the same contact when their emails are equal after normalising: trim whitespace, drop a `mailto:` prefix (any case), keep only the address inside `<...>` when a display name is present, and lowercase the result.

Write two files:

1. `output/contacts_clean.csv` with the header `name,email,phone,company` and one row per contact, in the order each contact first appears in the input.
   - `email` is the normalised address.
   - `name` is taken from the first row for that contact, with leading and trailing whitespace removed and runs of internal spaces collapsed to one.
   - `phone` and `company` are the first non-empty value among that contact's rows (empty if none).
2. `output/notes.md`: a short note stating the number of input rows, the number of output rows, and the rule you used to decide that two rows are the same contact.

Do not modify `contacts.csv`.
