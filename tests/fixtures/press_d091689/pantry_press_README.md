# Press: the Joule Report's editions

An edition is one JSON document, `el.edition.v1`. It is written once and never
changed; a correction is a new revision. The two files under `vectors/` are
real editions written by the architect on 2026-10-10 from the scout wire and
the bank. They are the banked objects every side of the contract is tested
against (dashboard trap D-09-05-T: a cross-repo object gets a schema pin and a
banked vector on day one).

`vectors/digests.json` holds each vector's sha-256 over its canonical bytes:
`json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`
encoded as UTF-8. Note `ensure_ascii=False`: the documents carry non-ASCII
characters and an ASCII-escaping serialiser produces different bytes.

## el.edition.v1, as the vectors state it

- `schema` is exactly `"el.edition.v1"`. A reader that meets another value
  draws nothing and says so.
- `kind` is `daily`, `weekly`, `monthly` or `article`. `edition_date` is the
  Pacific date the edition is for. An article also carries `slug`.
- `masthead {title, strap}`; `lead` is a story; a daily also has `digest`
  (paragraphs), `side` (a story), `stories`, `feeds` (stories written from
  EnergyLake's own feeds), `changed {since, rows[{topic, before, after}]}` and
  `clock [{when, text, standing[]}]`. An article's `lead` may carry `numbers
  [{value, label}]`, and the document carries `limits` (what it does not know).
- A story is `{desk, headline, deck?, dateline?, wide?, blocks[]}`.
- A block is one of:
  - `{type:"p", text, standing?}` and `{type:"question", text}`. `text` is
    plain text; the only markup is `**bold**`. No HTML, no links.
  - `{type:"h", text}`, a sub-heading inside a long story.
  - `{type:"figure", title, sub, note, charts[]}`.
- `standing` is a list drawn from `bank`, `confirmed`, `reported`, `tip`.
  `bank` means the writer read the figure from EnergyLake's database in that
  run. A reader draws the tags as written and never upgrades one.
- A chart is data, never a picture: `{id, kind, ...}` with `kind` one of
  `bars`, `lines`, `track`. Bars and lines carry `x` (labels) or
  `x_start` + `x_step`, `y {unit, min, max}`, `series [{name, values[]}]`,
  optional `highlight` (an index) and `annotation_row`. A `null` value is a day
  the bank does not hold: it is drawn as a gap and never filled. A track
  carries `past [[lat, lon]]`, `now`, `forecast [{tau_h, lat, lon, vmax_kt}]`.
  Every chart or series names its `source` (bank table, dataset, series,
  `read_at`).
- `sources` and `disclaimer` are sentences the page prints.
