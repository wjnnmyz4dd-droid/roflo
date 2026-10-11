# Repairing your DeskPilot installation

Your DeskPilot was installed, but neither of its two background services was
running. This page is the whole procedure. It keeps your database, your owner
credentials, your settings and your backups.

You do not need to uninstall anything first, and you must not delete the
`C:\DeskPilot` folder.

---

## What was wrong

DeskPilot runs as two background programs:

| | What it does |
|---|---|
| **DeskPilot** | the runtime — does the actual work |
| **DeskPilot Control Centre** | the web page you open at `127.0.0.1:8765` |

The runtime was being started with one invalid option, so Windows started it,
it stopped immediately with an error, and after ten attempts Windows gave up.
The control centre was never set up as a background program at all, which is
why your browser said **ERR_CONNECTION_REFUSED** — there was genuinely nothing
listening.

Both are fixed, and a third thing is too: the installer now starts both
programs when it finishes, so you no longer have to restart the machine before
anything works.

---

## Before you start

- Sign in to the VPS as an administrator.
- Have the control-centre password you want to use in mind. The repair sets it
  to whatever you type, so use your existing one if you want nothing to change.
- You do **not** need to stop MetaTrader. Nothing here touches it.

---

## Step 1 — check the file you downloaded

Open **Command Prompt** and run:

```
certutil -hashfile %USERPROFILE%\Downloads\DeskPilot-Setup.exe SHA256
```

Compare the long value it prints with the SHA-256 given to you alongside the
file. If they differ, stop and ask — do not run it.

## Step 2 — run it

Right-click `DeskPilot-Setup.exe` and choose **Run as administrator**.

On the first screen you should see `Existing DeskPilot    UPDATE AVAILABLE -
your data will be kept`. That line is how you know it has found your current
installation rather than treating this as a fresh machine.

## Step 3 — the Owner Setup screen

Fill in the same name and business email as before, and type the
control-centre password you want. Your **owner signing key is kept** either
way — the repair does not replace it, so everything already recorded stays
valid.

## Step 4 — the AI Setup screen

Leave it as it was. If you were using Ollama, choose Ollama again.

## Step 5 — let it finish

Before changing anything, the installer runs its own checks and will stop
without touching your machine if any of them fail. If it stops, read the lines
on screen: they say which check failed and why.

On the last screen you should now see:

```
Autostart         DeskPilot, DeskPilot Control Centre registered
Control centre    answering on http://127.0.0.1:8765
DESKPILOT READY
```

**`Control centre — answering`** is the line that matters. That is the
installer having actually connected to the control centre, not merely having
set it up.

---

## Checking it worked

Open `http://127.0.0.1:8765` in the browser on the VPS. You should get the
sign-in page.

To see both programs in Windows: press `Win+R`, type `taskschd.msc`, press
Enter. You should see **DeskPilot** and **DeskPilot Control Centre**, both
with Status `Running`.

To check from the command line:

```
schtasks /Query /TN "DeskPilot"
schtasks /Query /TN "DeskPilot Control Centre"
```

---

## If the control centre still does not answer

The installer may say `Control centre — not answering yet`. That is not
necessarily a failure: it can take a few seconds to start, and the installer
would rather tell you it cannot be sure than guess.

1. Wait a minute and refresh `http://127.0.0.1:8765`.
2. If it still will not connect, open Task Scheduler (`taskschd.msc`), click
   **DeskPilot Control Centre**, and look at the **History** tab. The last
   run's result explains what happened.
3. Send that result and `C:\DeskPilot\Logs\install.log`. The log never
   contains your password, your owner key or any API key.

---

## What this does not change

- Your database at `C:\DeskPilot\Data\solvent.db`.
- Your owner signing key in `C:\DeskPilot\Config\solvent.env`.
- Your backups in `C:\DeskPilot\Backups`.
- MetaTrader: not stopped, not restarted, not reconfigured. Its files,
  Expert Advisors, broker settings and firewall rules are untouched, and
  DeskPilot never adopts MetaTrader's copy of Python.
- Your firewall: the one rule DeskPilot adds *blocks* inbound connections to
  itself from outside this machine. Port 8765 is not opened to the network;
  the control centre listens on `127.0.0.1` only, which means this machine
  only.
- Real business activity stays switched off until you authorise it. Starting
  these two programs does not authorise anything: the control centre cannot
  decide anything by design, and the runtime's spending and submission
  controls are unchanged.

---

## One thing worth knowing

This installer is **not code-signed**, because no code-signing certificate
exists for DeskPilot yet. Windows will warn you that the publisher is unknown
and you will have to click through **More info → Run anyway**. That warning is
accurate and you should keep checking the SHA-256 in Step 1 — that is your
real assurance the file is the one intended, and it is the reason Step 1 is
first.
