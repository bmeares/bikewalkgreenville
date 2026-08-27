#!/usr/bin/env -S uv run --quiet --with google-api-python-client --with google-auth --script
"""Upload an .aab to a Google Play track.

One-time setup: service account with "Release to testing tracks" on the app,
key JSON at ~/.config/bwg/play-service-account.json (NEVER in this repo -- it's public).

    ./tools/play_upload.py build/app/outputs/bundle/release/app-release.aab
    ./tools/play_upload.py <aab> --track alpha --notes "fixed the map"
"""
import argparse
import os
import sys

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

PACKAGE = "org.bikewalkgreenville.app"
KEY = os.path.expanduser(os.environ.get("PLAY_KEY", "~/.config/bwg/play-service-account.json"))

p = argparse.ArgumentParser()
p.add_argument("aab")
p.add_argument("--track", default="internal")
p.add_argument("--notes", default=None, help="release notes (en-US)")
p.add_argument("--dry-run", action="store_true", help="upload to a draft edit, then discard")
a = p.parse_args()

for path, what in ((a.aab, "bundle"), (KEY, "service account key")):
    if not os.path.exists(path):
        sys.exit(f"missing {what}: {path}")

creds = service_account.Credentials.from_service_account_file(
    KEY, scopes=["https://www.googleapis.com/auth/androidpublisher"]
)
edits = build("androidpublisher", "v3", credentials=creds, cache_discovery=False).edits()

edit_id = edits.insert(packageName=PACKAGE, body={}).execute()["id"]
bundle = edits.bundles().upload(
    packageName=PACKAGE,
    editId=edit_id,
    media_body=MediaFileUpload(a.aab, mimetype="application/octet-stream", resumable=True),
).execute()
code = bundle["versionCode"]
print(f"uploaded versionCode {code}")

if a.dry_run:
    edits.delete(packageName=PACKAGE, editId=edit_id).execute()
    sys.exit(f"dry run: edit discarded, nothing released (versionCode {code} is now burned)")

release = {"versionCodes": [str(code)], "status": "completed"}
if a.notes:
    release["releaseNotes"] = [{"language": "en-US", "text": a.notes}]
edits.tracks().update(
    packageName=PACKAGE, editId=edit_id, track=a.track, body={"releases": [release]}
).execute()
edits.commit(packageName=PACKAGE, editId=edit_id).execute()
print(f"released versionCode {code} to {a.track}")
