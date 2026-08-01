# Official consular form templates

Ellis fills the **government's own blank PDF** for in-person visa routes. It
never draws a look-alike of an official form: a home-made facsimile is not the
official document, and presenting one at a consulate is a rejection at best and
can be treated as a forged document at worst.

Drop an official blank here and Ellis fills its real AcroForm fields. Without a
blank, Ellis produces a clearly-labelled *preparation sheet* instead — correct
data, honestly not claiming to be the official form.

## Adding a form

1. **Download the official blank** from the issuing authority itself (not a
   third-party copy), e.g.:

   | Form | Key | Official source |
   |---|---|---|
   | Schengen uniform visa application | `schengen_uniform` | The consulate/EU Commission page for the Schengen application form |
   | US DS-160 | `ds160_prep` | Online-only at `ceac.state.gov/genniv` — **no paper version exists**, so this key stays a preparation sheet by design |

   Save it as `<form_key>.pdf`, e.g. `schengen_uniform.pdf`.

2. **Check it is fillable and list its real field names:**

   ```bash
   python -m app.consular_forms inspect schengen_uniform
   ```

   A flattened scan has no AcroForm fields and cannot be filled — get the
   fillable version from the authority.

3. **Write the field map** as `<form_key>.map.json`, mapping the PDF's own
   field names (from step 2) to Ellis answer keys:

   ```json
   {
     "fields": {
       "Nachname": "surname",
       "Vornamen": "given_names",
       "Geburtsdatum": "birth_date",
       "Reisedokument_Nummer": "passport_number"
     }
   }
   ```

   Only keys in Ellis's own form vocabulary are accepted; anything else is
   rejected rather than silently ignored.

4. **Validate before it touches a real case:**

   ```bash
   python -m app.consular_forms validate schengen_uniform
   ```

   This fails loudly if the PDF is missing, is not fillable, maps a field the
   template does not have, or maps an answer key Ellis does not know.

## Guarantees

- A field with no applicant answer is left **blank** for them to complete — no
  value is ever invented, because they sign this under penalty of perjury.
- Required fields the applicant has not answered are reported as
  `missing_required` so the case asks them **before** they travel to an
  appointment with an incomplete form.
- Output is deterministic: the same answers always produce the same bytes.

Templates are **not** committed to this repo — each authority's form is its own
document with its own terms. This directory holds only this README plus the
maps/templates an operator adds.
