# Installing or repairing DeskPilot

One file, `DeskPilot-Setup.exe`. Double-click it. It works out whether
DeskPilot is already on the computer and offers the right thing.

There is no Command Prompt, no PowerShell and nothing to edit.

---

## If DeskPilot is already installed

The installer finds it and offers **Repair**, which is almost always what you
want. Repair keeps:

- your database and job history
- your control-centre password and your owner signing key
- your API keys and your settings
- your backups

It does not ask you anything. It cannot, because it reads all of it from the
installation it is repairing — so there is no screen on which you could type a
wrong answer over a right one.

What it replaces: the program files, the Python environment, the firewall
rule, and the two background services. That is the whole of what goes wrong.

Before it changes anything it copies your database to
`C:\DeskPilot\Backups\pre-repair-<date>.db` and moves the old program files
aside. If any step fails, both are put back and the screen says what failed.

## If DeskPilot is not installed

It installs cleanly and asks for three things: your name, your business email,
and a password for the control centre. Then which AI backend to use. That is
all.

---

## The steps

**1. Check the file is the one intended.** Open Command Prompt once — this is
the only command in this document, and it reads a file without changing
anything:

```
certutil -hashfile %USERPROFILE%\Downloads\DeskPilot-Setup.exe SHA256
```

Compare it with the SHA-256 you were given. If they differ, stop and ask.

This matters more than usual: the installer is **not code-signed**, so Windows
will say the publisher is unknown and you will have to click **More info → Run
anyway**. The hash is your real assurance that the file is the right one, which
is why it comes first.

**2. Right-click the file and choose Run as administrator.**

**3. Read the first screen.** It lists what it found: Windows version, Python,
whether DeskPilot is already here, and whether MetaTrader is present. If
MetaTrader is there it will say `MetaTrader detected - WILL NOT BE MODIFIED`.

**4. Choose Repair or Install.** This screen appears only if DeskPilot is
already installed. Repair is selected for you.

**5. Answer the questions, if you are asked any.** A repair asks nothing.

**6. Let it run.** Before changing anything it runs eight checks and stops
without touching the machine if any of them fail. If it stops, the lines on
screen say which check failed and why.

**7. Read the last screen.** This is the one that matters:

| It says | It means |
|---|---|
| **DeskPilot is installed** / **is repaired** | the checks passed, including the control centre answering on `127.0.0.1:8765` |
| **DeskPilot needs attention** | the files are in place and something did not pass. Do not assume it is running. The line underneath names the check |

The installer asks the control centre whether it answers, rather than assuming
it does because a service was created. If it cannot get an answer it says so.

**8. Click "Open the DeskPilot control centre."**

---

## Checking it yourself

Open `http://127.0.0.1:8765` on that computer. You should get the sign-in page.

To see the two services: press `Win+R`, type `taskschd.msc`, press Enter. Look
for **DeskPilot** and **DeskPilot Control Centre**, both `Running`.

## If the control centre does not answer

The last screen may say `not answering on 127.0.0.1:8765 yet`. That is not
necessarily a failure — a service can take a few seconds to bind its port, and
the installer would rather say it cannot be sure than guess.

1. Wait a minute and refresh the page.
2. Still nothing: open `taskschd.msc`, click **DeskPilot Control Centre**, and
   read the **History** tab. The last run's result says what happened.
3. Send that, plus `C:\DeskPilot\Logs\install.log`. The log never contains
   your password, your owner key or any API key.

---

## What is never touched

- **MetaTrader 5.** Not stopped, not restarted, not reconfigured. Its files,
  Expert Advisors, broker settings, accounts and firewall rules are left alone,
  and DeskPilot will not adopt MetaTrader's copy of Python even if that is the
  only Python on the machine. The installer detects MetaTrader for one reason:
  to say on screen that it is leaving it alone.
- **Your working Python and Ollama.** If a compatible one is there it is used,
  not replaced. Nothing is uninstalled.
- **Your firewall.** One rule is added and it *blocks* inbound connections to
  DeskPilot from outside this machine. Port 8765 is never opened. The control
  centre listens on `127.0.0.1`, which means this computer only.
- **Real business activity.** Applications, bids, messages, payments and
  spending stay off until you authorise them. Starting the two services does
  not authorise anything.

## The second opinion is included

DeskPilot checks its own work, and for a job above its confidence
threshold it wants a second reader -- one written by somebody else --
to look at the file before it goes to a client. Four of those come
with the installer: openpyxl for workbooks, python-docx for
documents, CleverCSV for CSV, markdown-it-py for Markdown.

You do not install them and there is no `pip install` step. They
travel inside the same file you downloaded, their SHA-256 hashes are
checked twice before anything is unpacked, and the installer
contacts nothing to get them.

The last screen reports what the second opinion covers. If it says
fewer than four formats, DeskPilot still runs -- it will decline a
job that needs a reader it has not got, while that job is still a
quote, rather than discovering it at delivery.

## Several people, several installations

Each installation generates its own owner key on its own machine and keeps its
own database, credentials and settings. There is no account server, no licence
check and nothing to register — the installer never contacts anything except
python.org, and only then if no suitable Python is already present. The
second-opinion readers and the timezone database travel inside the installer
rather than being fetched, so a machine with no internet at all installs the
same DeskPilot as one with.

One caveat, stated because it is a real limit: the installation directory is
fixed at `C:\DeskPilot`, so this is one installation per Windows machine or per
user profile on separate machines — not two side by side on one.
