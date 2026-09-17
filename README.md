# Bizim Trivia

A two-player trivia game generated from a WhatsApp chat export. Everything runs
locally: the export is parsed by a Python script and the result is a single
`index.html` you open on a phone. No server, no network, nothing leaves the machine.

## Quick start

1. In WhatsApp open the chat → **Export chat** → **Without media**. You get `_chat.txt`.
2. Put it in `data/_chat.txt` (the `data/` folder is gitignored).
3. Build:

   ```bash
   python3 build.py data/_chat.txt --rename "toghrul=Toğrul"
   ```

4. Open `dist/index.html` in a browser, or AirDrop / send it to a phone and open it there.

Requires Python 3.8+, no dependencies (encryption is pure standard library).

## Rounds

| Round | What it asks |
|---|---|
| Statistika | Who texts more at night, who starts conversations, who replies faster, the only days you did not text, first "sevirəm", longest call, favourite emoji, signature words… |
| Kim yazıb? | A real message, guess who wrote it |
| Boşluğu doldur | A message with one word hidden, four candidate words |
| Cavab nə olub? | A message and four real replies, pick the one that was actually sent |
| Bu söz kimindir? | A word only one of you uses |
| Hansı gün olub? | Three messages from one day, guess the date |
| Xüsusi suallar | Your own hand-written questions |

Questions are drawn randomly from the pools every game, so replays differ.

Word counts are spelling-insensitive: `günaydın`, `gunaydin` and `günayydin`, or `çox`,
`chox` and `cox`, count as one word. Azerbaijani letters are folded to plain Latin and
the `sh`, `ch`, `gh` keyboard spellings are folded too. The game still shows each person's
own spelling, and reveal texts list the variants that were merged.

## Modes

- **Növbə ilə / Take turns**: pass the phone. A question about one person is always
  asked to the other one. Separate scores, a winner at the end.
- **Birlikdə / Together**: one shared score.

UI is in Azerbaijani with an English toggle. Light and dark theme.

## Custom questions

Copy `custom_questions.example.json` to `data/custom_questions.json` and add your own.
Each item has `q`, `options`, `answer` (0-based index), optional `reveal`, and optional
`about` (a player's display name, or `"both"`). Strings can be plain or `{"az": …, "en": …}`.

## Keeping things out of the game

Random rounds quote real messages. To keep certain topics out, create
`data/exclude.txt` with one word or phrase per line. Any message containing one of
them is never shown in the game. Lines starting with `#` are ignored.

## Publishing (GitHub Pages)

A Pages site is public at a predictable URL, so never publish the plain build.
Build with a passphrase instead; the question data is then AES-256-GCM encrypted
and the page asks for the passphrase before it shows anything:

```bash
python3 build.py data/_chat.txt --rename "toghrul=Toğrul" --password "your passphrase" --out dist/public/index.html
```

Put that `index.html` on the `gh-pages` branch. The workflow in that branch
(`.github/workflows/pages.yml`) deploys it to GitHub Pages on every push. The
passphrase is remembered for the browser session only.

The plain build (no `--password`) is for opening the file directly on a phone.

## Options

```
python3 build.py data/_chat.txt
    --out dist/index.html          output file
    --rename "raw name=Display"    rename a participant (repeatable)
    --custom data/custom_questions.json
    --exclude data/exclude.txt
    --seed 7                       reproducible question pools
    --password "..."               encrypt the data; page asks for it on open
```

## Export format

Tested with the iOS WhatsApp export format:

```
[DD.MM.YY, h:mm:ss AM] Name: message
```

Media, calls, deleted and edited messages are detected and used for statistics but
never quoted. Multi-line messages are supported.
