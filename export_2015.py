#!/usr/bin/env python3
"""Export Watchtower Library 2015 (URES .PUB files) to the same SQLite schema as export_sqlite.py.

usage: export_2015.py <wtlib_2015 zip> <out.sqlite>
"""
import os, sqlite3, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collections import Counter

import export_sqlite as X
import wt2015 as W


def text(codes):
    return X.plain(codes).strip() if codes else None


def main(zpath, out):
    if os.path.exists(out): os.remove(out)
    db = sqlite3.connect(out)
    db.executescript(X.SCHEMA + 'CREATE TABLE errors(pub TEXT, doc TEXT, error TEXT);')
    unknown, sample = Counter(), {}
    z = zipfile.ZipFile(zpath)
    pubs = sorted(n for n in z.namelist() if n.upper().endswith('.PUB'))
    for name in pubs:
        pdir = name.split('/')[-2]
        d = z.read(name)
        try:
            ndocs, secs = W.ures_sections(d)
            info = W.pub_info(d, secs)
        except Exception as e:
            db.execute('INSERT INTO errors VALUES(?,?,?)', (pdir, None, repr(e))); continue
        pub_id = db.execute('INSERT INTO publications(dir,symbol,title,is_bible,doc_count) VALUES(?,?,?,?,?)',
                            (pdir, text(info.get(0xc8)), text(info.get(0xc5)) or text(info.get(0xc6)),
                             None, ndocs)).lastrowid
        n_ok = 0
        try:
            for seq, (dname, dclass, codes) in enumerate(W.pub_documents(d)):
                if codes is None: continue
                fields, pars = X.paragraphs(codes, unknown, sample)
                title = next((p['text'] for p in pars if p['cls'] == 'title'), pars[0]['text'] if pars else '')
                pages = [p['page'] for p in pars if p['page'] is not None]
                doc_id = db.execute('INSERT INTO documents(pub_id,seq,name,title,doc_ref,date,first_page,last_page) '
                                    'VALUES(?,?,?,?,?,?,?,?)',
                                    (pub_id, seq, dname, title, fields.get('doc_ref'), fields.get('date'),
                                     min(pages) if pages else None, max(pages) if pages else None)).lastrowid
                db.executemany('INSERT INTO paragraphs(doc_id,seq,class,style,page,text) VALUES(?,?,?,?,?,?)',
                               [(doc_id, j, p['cls'], p['style'], p['page'], p['text']) for j, p in enumerate(pars)])
                n_ok += 1
        except Exception as e:
            db.execute('INSERT INTO errors VALUES(?,?,?)', (pdir, str(n_ok), repr(e)))
        db.commit()
        print('%-12s %-50s %4d/%d docs' % (pdir, (text(info.get(0xc5)) or '')[:50], n_ok, ndocs), flush=True)
    db.execute("INSERT INTO paragraphs_fts(paragraphs_fts) VALUES('rebuild')")
    db.executemany('INSERT INTO unknown_codes VALUES(?,?,?)',
                   [('%04x' % c, n, X.plain(sample[c])) for c, n in unknown.most_common()])
    db.commit()
    print('unknown codes:', len(unknown), sum(unknown.values()))


if __name__ == '__main__':
    main(*sys.argv[1:3])
