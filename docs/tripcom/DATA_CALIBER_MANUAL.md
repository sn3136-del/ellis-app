# Data Caliber Manual — T-Station Visa Information Base

## The 25-field record
Every served answer is expressible as records of the 25-field dictionary
defined in the requirements specification, one record per visa product.
Field names, order and enumerations follow the specification exactly
(see backend/app/visa_snapshot/tstation.py, the single mapper). Units:
validity_unit in Day/Month/Year; max_stay_unit in Hour/Day (month- and
year-denominated stays are converted to days); visa_fee_currency is
ISO 4217; application_method in Embassy Submission / Online Application /
Agency Service / On-arrival Processing / Other.

## Verification tiers (source_check on every record)
1. human-quote: a person verified the record's fields against the named
   official page and quoted it. Highest tier; renders as High confidence.
2. grounded-consistent: the pipeline fetched the record's official page and
   found the stored answer consistent with it, figure by figure.
3. reference: an official government page is linked but has not yet been
   machine-compared to this record.
4. unchecked: no source page is linked yet.

## Confidence levels
High: human-verified against an official source, or single official source
with complete, conflict-free information. Medium: engine answer with
official reference, some fields pending. Low: conflicting or unverifiable;
BLOCKED from customer display until operations confirms (the reader sees an
honest "being verified" card, never the claims).

## Source rules
Official (government) sources only ever qualify as verification: gov.* /
go.* / gouv.* / gob.* and named state domains (the vetted list lives in
backend/app/visa_snapshot/authority.py). Commercial sites (VFS, iVisa,
wikis, news) never qualify. Every verified correction carries source_url,
verified_at and a note; the loader drops any entry that does not.

## Freshness policy
Every answer is re-checked against its official page after it is first
generated, and again on access once its freshness window lapses. Fee,
validity and stay figures are compared digit for digit; a page-contradicted
field is corrected with a verbatim quote; a conflict with a human
verification goes to the correction queue for a person. The machine never
silently outvotes a human check. Change events are recorded in the change
log (add / modify / delete with field-level diffs).
