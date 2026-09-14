"""Author Milo's question bank: python3 -m evaluations.bank_build writes evaluations/bank.jsonl.

Each row: id, q (the spoken question), cat (a category), optional route (the expected router
route), optional ideal (what a strong answer contains), optional after (the id of the turn whose
question and answer precede this one as conversation history).
"""
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
rows = []


def add(cat, items, route=None):
    for it in items:
        if isinstance(it, str):
            q, ideal, after = it, None, None
        else:
            q, ideal = it[0], it[1]
            after = it[2] if len(it) > 2 else None
        rid = f"{cat}-{len([r for r in rows if r['cat'] == cat]) + 1:02d}"
        row = {'id': rid, 'q': q, 'cat': cat}
        if route:
            row['route'] = route
        if ideal:
            row['ideal'] = ideal
        if after:
            row['after'] = after
        rows.append(row)


add('fact', [
    'What is the capital of Australia?', 'Who wrote Pride and Prejudice?',
    'How many bones are in the adult human body?', 'What year did the Berlin Wall fall?',
    'What is the largest planet in the solar system?', 'Who discovered penicillin?',
    'What is the speed of light?', 'How long does it take light from the Sun to reach Earth?',
    'What is the population of Canada?', 'Who was the first person to walk on the Moon?',
    'What is the chemical symbol for gold?', 'How deep is the Mariana Trench?',
    'Which country has the most time zones?', 'Who painted The Starry Night?',
    'What is the longest river in South America?', 'When was the Declaration of Independence signed?',
    'What language has the most native speakers?', 'What is the smallest country in the world?',
    'How many strings does a standard bass guitar have?', 'Who invented the World Wide Web?',
    'What is the freezing point of water in Kelvin?', 'How far is the Moon from Earth?',
    'What does DNA stand for?', 'Which element has atomic number 6?', 'Who directed Jurassic Park?',
    'What is the tallest building in the world?', 'How many players are on a soccer team on the field?',
    'What is the currency of Japan?', 'Who composed the Four Seasons?', 'What causes the seasons on Earth?',
], route='library')
add('definition', [
    'What does the word ubiquitous mean?', 'Define the word laconic.', 'What does ephemeral mean?',
    'What is a mutex?', 'What does idempotent mean in programming?', 'What is a race condition?',
    'What is inflation?', 'What does the term opportunity cost mean?', 'What is a black hole?',
    'What does sycophant mean?',
], route='library')
add('command', [
    ("What's the command to list my systemd user timers?", 'thought: systemctl --user list-timers'),
    ('How do I undo my last git commit but keep the changes?', 'thought: git reset --soft HEAD~1'),
    ('What is the pacman command to remove orphaned packages?', 'thought like: sudo pacman -Rns $(pacman -Qtdq)'),
    ('How do I reload the Hyprland config?', 'thought: hyprctl reload'),
    ('How do I install ffmpeg on Arch?', 'thought: sudo pacman -S ffmpeg'),
    ('How do I see which process is using port 8080?', 'thought: ss -ltnp | grep 8080 or lsof -i :8080'),
    ("What's the command to check disk usage of the current folder?", 'thought: du -sh .'),
    ('How do I list all files recursively including hidden ones?', 'thought: ls -laR or find .'),
    ('Give me the command to follow the journal for a user unit called milo-demos.', 'thought: journalctl --user -u milo-demos -f'),
    ('How do I create a new git branch and switch to it?', 'thought: git switch -c name'),
    ("What's the tar command to extract a .tar.gz file?", 'thought: tar -xzf file.tar.gz'),
    ('How do I find files bigger than one gigabyte?', 'thought: find / -size +1G'),
    ('How do I check my public IP from the terminal?', 'thought: curl ifconfig.me'),
    ("What's the command to update everything on Arch?", 'thought: sudo pacman -Syu'),
    ('How do I kill a process by name?', 'thought: pkill name'),
    ('How do I make a Python virtual environment?', 'thought: python -m venv .venv'),
    ("What's the docker command to list running containers?", 'thought: docker ps'),
    ('How do I search the Arch repos for a package?', 'thought: pacman -Ss name'),
    ('How do I see my GPU usage on Nvidia?', 'thought: nvidia-smi'),
    ('How do I restart a systemd user service?', 'thought: systemctl --user restart name'),
    ('How do I copy a file to a remote machine with scp?', 'thought: scp file user@host:/path'),
    ("What's the ffmpeg command to convert an mp4 to mp3?", 'thought: ffmpeg -i in.mp4 out.mp3'),
    ('How do I print the last 50 lines of a file and keep following it?', 'thought: tail -n 50 -f file'),
    ('How do I set a git remote URL?', 'thought: git remote set-url origin URL'),
    ("What's the command to see all listening ports?", 'thought: ss -tulnp'),
])
add('link', [
    ('Give me the link to the Arch wiki page on systemd timers.', 'thought link https://wiki.archlinux.org/title/Systemd/Timers; the spoken text never spells the url'),
    ('Send me the URL for the Hyprland wiki.', 'link https://wiki.hyprland.org'),
    ("What's the link to the Python documentation?", 'link https://docs.python.org'),
    ('Give me the URL of the Ollama GitHub repo.', 'link https://github.com/ollama/ollama'),
    ('Link me the Wikipedia article on the Roman Republic.', 'link https://en.wikipedia.org/wiki/Roman_Republic'),
    ('Where is the Arch wiki page for pacman?', 'link https://wiki.archlinux.org/title/Pacman'),
    ('Give me the link to Hacker News.', 'link https://news.ycombinator.com'),
    ("What's the URL for the systemd man page on timers?", 'link to the systemd.timer man page'),
    ('Send me a link to the Rust book.', 'link https://doc.rust-lang.org/book/'),
    ('Where can I download Neovim?', 'link https://neovim.io or the GitHub releases page'),
])
add('live', [
    "What's the weather in Portland today?", "What's Nvidia's stock price right now?",
    'How is the S&P 500 doing today?', "What's the exchange rate from dollars to euros?",
    'Who won the Bills game?', "What's the temperature outside?", 'Is the stock market open right now?',
    "What's Bitcoin trading at?", "What's the current time in Tokyo?", "What's the forecast for tomorrow in Portland?",
], route='web')
add('recent', [
    "What's in the news today?", 'Any recent news about Nvidia?', 'What happened in the world this week?',
    "What's new with SpaceX?", 'What are the latest headlines in tech?', 'Anything new on the Linux desktop lately?',
    "What's going on with the Fed?", 'Did anything big happen in AI this week?',
])
add('reminder', [
    'Remind me to take the trash out at 7 pm.', 'Set a timer for 10 minutes.',
    'Remind me tomorrow morning to call the dentist.', 'What time is it?', 'What day is it today?',
    'Remind me in 20 minutes to check the oven.', 'Wake me up at 6 am.', 'Set a reminder for Friday to pay rent.',
])
add('calc', [
    'What is 15 percent of 240?', "What's 7 times 86?", 'What is 2 to the power of 10?',
    "What's 1000 divided by 8?", "What's the square root of 144?", 'How much is 45 plus 87?',
    'What is 3.5 times 12?', "What's 20 percent off of 150 dollars?", "What's 365 minus 128?",
    'How much is 12 percent of 3500?',
])
add('convert', [
    'How many kilometres is 30 miles?', 'How many ounces are in a pound?', 'What is 100 Fahrenheit in Celsius?',
    'How many cups are in a litre?', 'How many seconds are in a day?',
])
add('opinion', [
    ("Who's better, Michael Jordan or LeBron James?", 'takes a side'),
    ('Should I learn Rust or Go?', 'picks one with a reason'),
    ("What's your opinion on Windows?", 'has a take'),
    ('Is Arch Linux worth it for a beginner?', 'a clear answer'),
    ("What's the best text editor?", 'picks one'),
    ('Do you think AI will take programming jobs?', 'takes a position'),
    ('Which is better, coffee or tea?', 'picks'),
    ("What's your favorite movie?", 'picks one and stays in character'),
    ('Should I buy a Steam Deck or a gaming laptop?', 'picks with a reason'),
    ('Is it better to rent or buy a house right now?', 'gives a view with caveats, no advice dodge'),
    ("What's the most overrated programming language?", 'names one'),
    ('Cats or dogs?', 'picks'),
])
add('workspace', [
    'What is the status of Milo?', "What's left on the Milo roadmap?", 'What does the agent-harness repo do?',
    "What's next for the media vault project?", 'What is in my workspace?', "What's the state of the local-ai-lab repo?",
], route='workspace')
add('self', [
    ('Who are you?', 'Milo, the local voice companion'),
    ('What can you do?', 'lists real abilities: offline library, web search, news, reminders, calculator, workspace notes, commands and links in the bubble'),
    ('Do you have access to Wikipedia?', 'yes, an offline copy'),
    ('Can you search the internet?', 'yes, when asked or for live facts'),
    ('What model are you running on?', 'a local Gemma model on this machine'),
    ('Do you remember our earlier conversations?', 'a recap of recent questions and memory notes'),
    ('Are you listening all the time?', 'no, push to talk'),
    ("What's your personality?", 'in character'),
    ('Can you run commands on my computer?', 'honest: shows commands in the bubble, does not run them'),
    ('Where does my data go when I talk to you?', 'stays local except web searches'),
])
add('followup', [
    ("What's the tallest mountain in Africa?", None),
    ('How tall is it?', '5,895 m or 19,341 ft; understands it', 'followup-01'),
    ('Who wrote The Old Man and the Sea?', None),
    ('When was he born?', '1899, Hemingway', 'followup-03'),
    ("What's the capital of Peru?", None),
    ("What's the population there?", 'about 10 million for Lima', 'followup-05'),
    ('How do I undo my last git commit but keep the changes?', None),
    ('And what if I want to throw the changes away too?', 'thought: git reset --hard HEAD~1', 'followup-07'),
    ('Should I learn Rust or Go?', None),
    ('Why not the other one?', 'defends the pick', 'followup-09'),
    ('What is a mutex?', None),
    ('Give me an example in Python.', 'a thought with code', 'followup-11'),
    ("What's 15 percent of 240?", None),
    ('And 20 percent?', '48', 'followup-13'),
    ('Who painted the Mona Lisa?', None),
    ('Where is it now?', 'the Louvre in Paris', 'followup-15'),
])
add('smalltalk', [
    'Hey Milo.', 'Good morning.', 'Thanks, that was helpful.', 'How are you doing today?', 'Tell me a joke.',
    "I'm bored.", 'Never mind.', 'Good night, Milo.',
])
add('garbled', [
    ("What's the Hyperland command to reload the config?", 'understands Hyprland; thought: hyprctl reload'),
    ('How do I remove orphaned packages with Pac-Man?', 'understands pacman'),
    ("What's the command to list my systemmd user timers?", 'systemd'),
    ('Give me the link to the Arch wiki page on system empty timers.', 'systemd timers link'),
    ('How do I install Neo vim?', 'Neovim; pacman -S neovim'),
    ('What is the boiling point of water in fair and height?', '212 F'),
    ('Who is healthy gamer GG?', 'Dr K, the psychiatrist YouTuber; should look up rather than shrug'),
    ('What is a lama in AI?', 'Llama models'),
    ('How do I use bit rifts snapshots?', 'btrfs snapshots'),
    ("What's the weather in Portland today", 'route web'),
])
add('webask', [
    'Search the web for the best pizza in Portland.', 'Google how to fix a leaking faucet.',
    'Look online for the release date of the next Zelda game.', 'Search for reviews of the Framework laptop.',
    'Can you look up on the internet who owns Anthropic?', 'Search the web for CachyOS scheduler options.',
    'Web search: cheapest flights from Portland to Denver.', 'Look it up online: how long do lithium batteries last?',
    'Search the internet for Pocket TTS.', 'Find on the web the population of Portland Oregon.',
], route='web')
add('depth', [
    ('Tell me about the history of the internet in detail.', 'a longer answer, several sentences'),
    ('Explain how TCP handshakes work step by step.', 'three steps'),
    ('Walk me through how systemd boots a Linux system.', 'ordered, several sentences'),
    ('Give me a thorough explanation of how public key cryptography works.', 'longer'),
    ('Compare Wayland and X11 with the tradeoffs.', 'a comparison'),
    ('Prove that the square root of 2 is irrational.', 'a proof sketch'),
    ('Write a Python function that reverses a linked list.', 'a thought with code'),
    ('Explain the difference between processes and threads carefully.', 'a clear contrast'),
])
add('spelling', [
    ('How do you spell accommodate?', 'spells it as letters'),
    ('How do you spell necessary?', None),
    ("Is it 'affect' or 'effect' when I mean influence?", 'affect'),
    ("How many m's are in recommend?", 'two'),
])
add('ambiguous', [
    ('What about the other one?', "asks what 'other one' means"),
    ('Do the thing.', 'asks what thing'),
    ("Okay, what's...", 'invites finishing the question'),
    ('Is it good?', 'asks what'),
    ('Can you fix it?', 'asks what'),
    ('Why?', 'asks about what'),
])
add('personal', [
    "What's my name?", 'What do you know about me?', 'What did I ask you about recently?',
    'What are my preferences?', 'Do I take my coffee black?',
])
add('long', [
    ('Tell me a story about a lighthouse keeper, at least six sentences.', 'at least six sentences'),
    ('List the planets in order from the sun.', 'all eight'),
    ('Name five Linux distributions and one line about each.', 'five'),
])


def main():
    out = HERE / 'bank.jsonl'
    with out.open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(len(rows), 'questions ->', out)
    print(dict(Counter(row['cat'] for row in rows)))


if __name__ == '__main__':
    main()
