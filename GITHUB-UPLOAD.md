# Publishing to GitHub — web UI only

No git commands. Everything below is done in a browser.

You will need: your GitHub account, and the file `hvac-coordinator.zip` from
this output.

---

## Step 1 — Unpack the zip

Unzip it somewhere you can find. You should get a folder `hvac-coordinator/`
containing:

```
hvac-coordinator/
├── .gitignore
├── ATTRIBUTION.md
├── GITHUB-UPLOAD.md
├── LICENSE
├── README.md
├── hacs.json
├── mypy.ini
├── custom_components/
│   └── hvac_coordinator/
│       ├── __init__.py
│       ├── config_flow.py
│       ├── const.py
│       ├── coordinator.py
│       ├── diagnostics.py
│       ├── entity.py
│       ├── hci.py
│       ├── icons.json
│       ├── manifest.json
│       ├── models.py
│       ├── modes.py
│       ├── py.typed
│       ├── quality_scale.yaml
│       ├── sensor.py
│       ├── services.yaml
│       ├── store.py
│       ├── strings.json
│       ├── tariff.py
│       └── translations/
│           └── en.json
├── docs/
│   └── (15 markdown files)
└── tests/
    ├── README.md
    ├── conftest.py
    ├── test_config_flow.py
    ├── test_core.py
    └── test_init.py
```

**Check `custom_components/hvac_coordinator/py.typed` is there.** It is an empty
file, and empty files are the ones a drag-and-drop upload is most likely to
drop silently. If it is missing, see step 5.

---

## Step 2 — Create the repository

1. Go to **https://github.com/new**
2. Repository name: `hvac-coordinator`
3. Description: `Home Assistant integration that decides what your air conditioning should be doing, room by room, and tells you why.`
4. **Public**
5. **Do not** tick "Add a README file"
6. **Do not** add a .gitignore or a licence — both are in the zip
7. Click **Create repository**

You land on a page headed "Quick setup". Leave it open.

---

## Step 3 — Upload the files

On that page, click **uploading an existing file**. (If you have navigated away:
**Add file → Upload files**.)

1. Open the unzipped `hvac-coordinator/` folder on your computer
2. Select **the contents**, not the folder itself — `custom_components`, `docs`,
   `tests`, `README.md`, `LICENSE`, `ATTRIBUTION.md`, `hacs.json`, `mypy.ini`,
   `GITHUB-UPLOAD.md`
3. Drag them onto the GitHub page

**Dragging the outer `hvac-coordinator` folder is the common mistake.** It
creates a `hvac-coordinator/` directory inside the repository, which puts
`custom_components` one level too deep and stops HACS finding the integration.

4. Under **Commit changes**, enter:

   ```
   Initial commit: HVAC Coordinator v0.3.0
   ```

5. Leave **Commit directly to the main branch** selected
6. Click **Commit changes**

### Verify before going further

On the repository home page, check the file list shows `custom_components`,
`docs` and `tests` **at the top level**. If you see a single
`hvac-coordinator` folder instead, the drag went wrong: delete the repository
and start again from step 2. That is faster than fixing it in the UI.

Then click into `custom_components/hvac_coordinator/` and confirm you can see
`manifest.json`, `py.typed` and the `translations` folder.

---

## Step 4 — Set the repository details

On the repository home page, click the gear icon next to **About**, top right.

- **Description:** as above
- **Topics:** add `home-assistant`, `hacs`, `hvac`, `climate-control`,
  `home-automation`, `python`
- Untick **Releases**, **Packages** and **Environments** — nothing uses them yet
- Save

---

## Step 5 — If `py.typed` did not upload

Empty files are sometimes skipped by the drag-and-drop uploader.

1. Navigate to `custom_components/hvac_coordinator/`
2. **Add file → Create new file**
3. Filename: `py.typed`
4. Leave the body **completely empty**
5. Commit directly to `main`

---

## Step 6 — Fix the URLs in the manifest

`manifest.json` currently points at `github.com/meremortals70/hvac-coordinator`.
If your username differs, this must be corrected or Home Assistant will link
users to a repository that does not exist.

1. Open `custom_components/hvac_coordinator/manifest.json`
2. Click the pencil icon
3. Correct `documentation`, `issue_tracker` and `codeowners` to your username
4. Commit directly to `main`

---

## Step 7 — Tag a release

HACS installs from releases.

1. On the repository home page, click **Releases** in the right-hand column,
   then **Create a new release**
2. **Choose a tag** → type `v0.3.0` → **Create new tag: v0.3.0 on publish**
3. Release title: `v0.3.0`
4. Description:

   ```
   First public release.

   Comfort index, room modes, actuator ordering and the full decision trace.

   Not yet wired to actuation — see docs/known-limitations.md. This release
   observes and explains; it does not yet change what your air conditioning does.
   ```

5. Tick **Set as a pre-release** — nothing here has been run in Home Assistant
   and the release notes should not imply otherwise
6. **Publish release**

---

## Step 8 — Install it in Home Assistant via HACS

1. HACS → three-dot menu, top right → **Custom repositories**
2. Repository: `https://github.com/YOUR_USERNAME/hvac-coordinator`
3. Type: **Integration**
4. **Add**
5. Find **HVAC Coordinator** in HACS and install it
6. Restart Home Assistant
7. **Settings → Devices & Services → Add Integration → HVAC Coordinator**

---

## If something goes wrong

The integration does not appear in the Add Integration list:

- Confirm `config/custom_components/hvac_coordinator/manifest.json` exists on
  the Home Assistant machine
- Confirm Home Assistant was fully restarted, not just reloaded
- Check the log for `hvac_coordinator`

HACS says the repository structure is wrong:

- `custom_components/hvac_coordinator/` must be at the top level of the
  repository, not nested inside another folder

Send me the actual error text rather than a description of it.
