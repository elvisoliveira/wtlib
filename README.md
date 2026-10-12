# wtlib-export

Converts the publication data of old **Watchtower Library** CD/DVD-ROM editions into an
open, queryable **SQLite** database (publications, documents, paragraphs, full-text search).

The original discs ship a Windows-only reader (Win16 for the 1990s editions) that no longer
runs on modern systems. This project reads the files directly so that the content you already
own stays accessible.

## No content is included

This repository contains **only code**. It does not include, and must not be used to
redistribute, any publication text, images, fonts, or program files from the discs.
The publications are copyrighted by their publisher. You need your own copy of the
CD/DVD-ROM to use these tools, and the generated database is for your personal use.

This is an independent project, not affiliated with or endorsed by the publisher.
It exists for interoperability and the preservation of legacy media. No encryption or
copy-protection is circumvented: the formats use data compression and a proprietary
character encoding, both reimplemented here from scratch.

## Supported editions

| Edition | Layout | Script |
|---|---|---|
| 1995 (Win16) | `WT_DATA_.E/<PUB>/DOCSFILE`, `PUBINFO` | `export_sqlite.py` |
| 2015 | `rs_data/PUBS/<PUB>/<PUB>.PUB` (URES container) | `export_2015.py` |

Other editions between these were not tested and may use other formats.

## Usage

Python 3.8+, standard library only.

```sh
# 1995 edition: point to the WT_DATA_.E directory copied from the disc
python3 export_sqlite.py /path/to/WT_DATA_.E wtlib_1995.sqlite

# 2015 edition: point to a zip of the disc contents (read in place, nothing is extracted)
python3 export_2015.py /path/to/wtlib_2015.zip wtlib_2015.sqlite
```

## Database schema

```
publications(id, dir, symbol, title, is_bible, doc_count)
documents(id, pub_id, seq, name, title, doc_ref, date, first_page, last_page)
paragraphs(id, doc_id, seq, class, style, page, text)
paragraphs_fts            -- FTS5 index over paragraphs.text (diacritics-insensitive)
unknown_codes(code, count, sample)   -- character codes with no Unicode mapping yet
```

`class` is one of `title`, `heading`, `question`, `footnote`, `body`.

```sh
sqlite3 wtlib_2015.sqlite "SELECT d.title, p.page, p.text
  FROM paragraphs_fts f JOIN paragraphs p ON p.id = f.rowid JOIN documents d ON d.id = p.doc_id
  WHERE paragraphs_fts MATCH 'integrity' LIMIT 5"
```

## Format notes

* **Characters** are stored in MEPS, a 16-bit encoding: Latin letters from `0x0800`, Greek
  from `0x0700`, Hebrew from `0x0600` (stored in visual order), markup from `0xFA00`.
  Diacritics precede the letter they modify. The mapping lives in `export_sqlite.py` and is
  shared by both editions.
* **1995**: text is compressed with a small byte → code-run dictionary stored in the
  `DOCSFILE` header.
* **2015**: each `.PUB` is a URES container; documents carry a header of bit-packed
  variable-length integers, and their text is compressed with a prefix code over MEPS
  code runs whose dictionary is itself LZ-compressed (`wt2015.py`).

Formatting (bold/italic), the per-document word indexes and multimedia are not exported.
A few rarely used symbols are still unmapped; they are listed in `unknown_codes`.

## License

MIT for the code in this repository. The license does not extend to any data read by it.
