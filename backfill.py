import re
import sys
from email_parser import supabase, get_service, walk, parse, SENDERS

APPLY = '--apply' in sys.argv
GMAIL_ID = re.compile(r'[0-9a-f]{16}')
PAGE = 1000


def fetch_all_rows():
    rows, start = [], 0
    while True:
        batch = (supabase.table('transactions')
                 .select('*')
                 .not_.is_('email_id', 'null')
                 .order('id')
                 .range(start, start + PAGE - 1)
                 .execute().data)
        rows.extend(batch)
        if len(batch) < PAGE:
            return rows
        start += PAGE


def main():
    svc = get_service()
    all_rows = fetch_all_rows()
    rows = [r for r in all_rows if GMAIL_ID.fullmatch(str(r['email_id']))]
    print(f"{len(all_rows) - len(rows)} non-Gmail rows (SMS shortcut) will be left untouched")
    print(f"Checking {len(rows)} email rows ({'APPLY' if APPLY else 'DRY RUN'})")

    deleted = updated = unchanged = skipped = 0

    for r in rows:
        try:
            msg = svc.users().messages().get(userId='me', id=r['email_id'], format='full').execute()
        except Exception as e:
            print(f"SKIP    {r['email_id']}: {e}")
            skipped += 1
            continue

        headers = {h['name'].lower(): h['value'] for h in msg['payload'].get('headers', [])}
        sender = headers.get('from', '').lower()
        subject = headers.get('subject', '')
        parts = []
        walk(msg['payload'], parts)
        body = ' '.join(parts) or msg.get('snippet', '')

        tx = None
        if any(s in sender for s in SENDERS):
            tx = parse(subject, body, msg.get('internalDate', 0))

        if tx is None:
            print(f"DELETE  {r['date']} Rs {r['amount']} | {str(r['description'])[:60]}")
            deleted += 1
            if APPLY:
                supabase.table('transactions').delete().eq('id', r['id']).execute()
            continue

        changes = {k: v for k, v in tx.items() if str(r.get(k)) != str(v)}
        if changes:
            print(f"UPDATE  {r['date']} Rs {r['amount']} | {str(r['description'])[:40]} -> {changes}")
            updated += 1
            if APPLY:
                supabase.table('transactions').update(changes).eq('id', r['id']).execute()
        else:
            unchanged += 1

    print(f"\nSummary: {deleted} delete, {updated} update, {unchanged} unchanged, {skipped} skipped")
    if not APPLY:
        print("Dry run only. Nothing was changed. Add --apply to write changes.")


if __name__ == '__main__':
    main()
