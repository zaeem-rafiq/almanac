"""Build the A-02 synthetic channel corpus.

The prose lives here and the artifacts are generated, deliberately:

* hand-typed SRT timestamps drift and overlap, and a corpus whose captions do not parse is
  worthless to A-03's extractor. Composing them with the `srt` library makes monotonic,
  non-overlapping cues structural rather than something a proofreader has to catch.
* `scripts/check_corpus.py` re-parses the written files from disk with `srt.parse`, so the
  proof tests the artifact, not this generator's intent.

RULE (Project Rules, "No invented facts"): every figure spoken below is either a `history`
value in facts/catalog.yaml, a current catalog value, or a figure quoted verbatim from a
source page that already serves this project. Provenance is recorded per script in NUMBERS.

Verified against srt 3.5.3: srt.Subtitle(index, start, end, content, proprietary='') and
srt.compose(subtitles, reindex=True, ...) — signatures read from the installed package.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import srt

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANNEL_DIR = REPO_ROOT / "corpus" / "channel"

WORDS_PER_CUE = 10
CUE_SECONDS = 2.8
GAP_SECONDS = 0.2


# --------------------------------------------------------------------------- scripts

V1 = """
Every January somebody in the comments tells me I have the 401k number wrong, so today we are
going to settle it. This is the contribution limit, what it actually means, and the three
mistakes I see people make with it every single year.
Here is the number. For 2024 you can defer twenty three thousand dollars of your own money into
your 401k. That is the elective deferral limit. It is the money that comes out of your paycheck
before you ever see it.
If you are fifty or older there is a catch up on top of that, and the catch up is seven thousand
five hundred dollars. So the older saver gets a meaningfully bigger bucket, and almost nobody
uses the whole thing.
Now the part that trips people up. That deferral limit is your money only. It does not include
what your employer puts in. The combined limit, yours plus theirs, is sixty nine thousand dollars
this year, and that is a completely separate ceiling.
There is one more threshold worth knowing about, and it has a boring name. If you earn more than
one hundred fifty five thousand dollars you are a highly compensated employee, and your plan may
limit what you can defer regardless of the headline number.
Okay, the three mistakes.
Mistake one is stopping at the match. Say you make seventy thousand dollars and your plan matches
six percent of your salary. Six percent is four thousand two hundred dollars of your own money.
That is a great start and it is nowhere near the limit.
Mistake two is front loading without checking your match formula. If you dump five hundred
dollars a month in January and hit the ceiling in September, some plans stop matching the moment
you stop contributing. You just left three months of free money on the table.
Mistake three is ignoring the tax side. Say you are in the twenty two percent bracket. Every
dollar you defer is a dollar you are not taxed on today, and that is the whole point of the
traditional account. If you would rather pay the tax now, that is what the Roth side of your plan
is for.
So what do you actually do with this. Log in. Find the contribution percentage, not the dollar
amount, because the percentage keeps up with your raises and the dollar amount does not. Raise it
one point. Then set a calendar reminder for next January to raise it one more.
One point a year is invisible in your paycheck and it is enormous over a decade. I have watched
people go from three percent to fifteen percent this way without ever once feeling poorer.
And check the limit every January, because it moves. It moved this year, it moved last year, and
it will move again. The number in this video is the number for this year only.
If this helped, the subscribe button is right there, and I read every comment. I am not a
financial advisor and this is not financial advice, it is just what I would tell a friend. Go
raise your contribution one point. I will see you in the next one.
"""

V2 = """
Pay off the mortgage or invest the money. This is the single most argued about question I get,
and most of the answers you have heard are missing the part that actually decides it.
Let us set the table. Say you have three hundred thousand dollars left on the loan, your payment
is about one thousand two hundred dollars a month before taxes and insurance, and you have found
an extra five hundred dollars a month you could throw at either side.
The rate on the loan is the whole argument. Right now the thirty year fixed average is six point
eight five percent. That is your guaranteed return for paying the loan down early. Guaranteed is
the word doing the work there.
On the other side, say you assume seven percent from a broad market index fund. I want to be
careful here, because that is an assumption I am making for the math, not a promise anybody made
to you. The market does not owe you seven percent in any particular decade.
So on paper it is close to a coin flip, and that is exactly why the spreadsheet is the wrong tool
for this decision.
Here is what the spreadsheet leaves out. Paying the mortgage down is a guaranteed return with no
sequence risk. Investing is an expected return with a very wide distribution. Those two things
are not the same kind of number, and comparing them directly is the mistake almost everybody
makes.
The second thing it leaves out is liquidity. Money you put into the house is gone until you sell
or borrow against it. Money in a brokerage account is there the week your roof fails.
And the third thing is the emotional return, which is not a real financial term and is absolutely
a real thing. Some people sleep better with no mortgage. That is worth something, and you are
allowed to buy it.
So here is the order I would actually use.
First, get the full employer match. That is not a rate comparison, that is free money, and it wins
against everything.
Second, build a cash buffer you would not feel embarrassed about. Whatever number makes you
willing to leave the extra five hundred dollars invested during a bad year, that is the right
number for you.
Third, then compare the rate. If your loan rate is meaningfully above what you expect from the
market, the loan is a fine place for the extra five hundred. If it is well below, invest and stop
thinking about it.
And a practical note. If you do put extra at the mortgage, tell your servicer to apply it to
principal. Otherwise a lot of them just park it against next month's payment and you get nothing
out of it. Fifteen years of extra payments applied to the wrong bucket is a real thing that
happens to real people.
What I would not do is agonize. Both of these are good outcomes. You are choosing between
guaranteed and probably better, and there is no version of this where you end up worse off than
the person who spent the money.
Usual disclaimer, I am not a financial advisor and this is not financial advice. If this was
useful, let me know which side you landed on. See you next time.
"""

V3 = """
The health savings account is the only account in the tax code that is tax free on the way in,
tax free while it grows, and tax free on the way out. Three times. Nothing else does that, and
most people who qualify for one are using it as a glorified debit card.
First, the numbers for this year. If you are on a self only high deductible plan you can put four
thousand three hundred dollars in. Family coverage is eight thousand five hundred fifty dollars.
Those are the ceilings, and they include anything your employer contributes.
Now the part almost nobody does. Do not spend it.
Here is the mechanic that makes this account extraordinary. There is no deadline to reimburse
yourself. If you pay a medical bill out of pocket today and you keep the receipt, you can pull
that money out of the account tax free in twenty years. The receipt does not expire.
So the play is, contribute the maximum, invest it like a retirement account, pay today's medical
bills from your regular cash, and keep a folder of receipts. You are building a pile of tax free
withdrawals you can trigger whenever you want.
Say your family puts in seven hundred dollars a month. That is not a small amount, and I know it.
But run the same number into a taxable brokerage account and compare, and it is not close, because
you never paid tax on the contribution in the first place.
And say you are in the twenty four percent bracket. Every dollar in is a dollar you are not taxed
on, so the account is effectively giving you a discount on it before it ever gets invested.
A few boundaries so nobody gets in trouble.
You have to be on a qualifying high deductible plan to contribute. Not to keep the account, not
to spend from it, just to put new money in.
Non medical withdrawals before sixty five get taxed and penalized. After sixty five it behaves
like a traditional retirement account for non medical spending, which is a fine consolation prize.
And if your plan has an executive tier, the key employee threshold of two hundred thirty
thousand dollars can change what your employer is allowed to do for you specifically. Ask your
benefits team, not the internet.
One more that is not an account rule but comes up constantly in the comments. The annual gift
exclusion is nineteen thousand dollars, so if a parent is helping fund your medical costs there
is a lot of room before anybody needs to file anything.
And if you are itemizing rather than taking the standard deduction, remember the standard
deduction for a single filer is fifteen thousand seven hundred fifty dollars, and medical
expenses have to clear a high floor before they do anything for you. For most people the standard
deduction wins and the health savings account is the better lever anyway.
So, three actions. Check whether your plan qualifies. Turn on investing inside the account,
because a shocking number of these sit in cash by default. And start the receipt folder today.
I am not a financial advisor and this is not financial advice. If you have been using this
account as a debit card, no judgment, just go change the investment setting. See you next time.
"""

V4 = """
Ten thousand dollars. It showed up, it is yours, and you have no idea what to do with it. This is
exactly what I would do, in order, and I am going to show you the arithmetic instead of just
telling you to invest it.
Ground rules first, because this whole video is hypothetical. Say you earn eighty thousand
dollars, you have no debt above eight percent, and this ten thousand is genuinely spare. Change
any of those and the order changes.
Step one. Put one thousand dollars somewhere boring and instantly reachable. Not invested. This is
the fund that stops a car repair from turning into a credit card balance, and one thousand is
enough to cover the ordinary version of almost every emergency.
Step two. Nine thousand left. Before anything clever, check whether you are getting your full
employer match at work. If you are not, redirect part of your paycheck to capture it and use this
cash to cover the gap in your budget. I am not going to pretend that is exciting, but it is the
highest return move on this list by a wide margin.
Step three. Whatever is left goes into three funds. Not thirty. Three. A broad domestic stock
fund, a broad international stock fund, and a bond fund. That is a complete portfolio, and every
additional fund you add past that is mostly there to make you feel busy.
Now the split, and here is where I have to be honest with you about assumptions. Say you go
sixty forty, sixty percent stocks and forty percent bonds. Or eighty twenty if you are younger and
you genuinely will not flinch. There is no correct answer here, there is only the answer you will
not abandon in a bad year.
Let us do the arithmetic on the boring version. Say the whole thing returns seven percent a year.
I want to be really clear, I made that seven percent up for the illustration. Nobody guarantees
it. Over thirty years, at that assumed rate, a lump sum roughly doubles about three and a half
times. That is the entire magic trick. It is not a strategy, it is just time.
And here is the part people underrate. Adding five hundred dollars a month to that same account
matters more than the lump sum does, over any reasonable horizon. The ten thousand is the
starting gun, not the race.
Step four. Do nothing. Genuinely. Set the automatic contribution, set a once a year reminder to
rebalance, and then go live your life. The single biggest destroyer of returns in every study I
have ever read is the investor, not the market.
What I would not do with ten thousand dollars. I would not put it in one company. I would not put
it in something a stranger messaged me about. And I would not wait for a better entry point,
because waiting for a better entry point is how people spend four years in cash.
Every number in this video was made up to illustrate the shape of the decision. Your numbers are
different, your risk tolerance is different, and I am not a financial advisor and this is not
financial advice. Do the boring version. See you next time.
"""

V5 = """
Inflation protected savings bonds versus a high yield savings account. Everybody asks about this
in the comments, and the honest answer is that it depends on a rate that changes twice a year and
a lockup most people do not read about until it is too late.
Let us start with what the bond actually pays. The composite rate right now is three point one
one percent. That rate is set for a six month window, and it resets on the first of May and the
first of November, so the number in this video has a shelf life measured in months, not years.
Now, the reason everybody heard about these in the first place. Back in 2022 it paid nine point
six two percent, which was an extraordinary number that had nothing to do with the bond being
clever and everything to do with inflation being high. That was a moment, not a feature, and I
think a lot of people bought in expecting it to be permanent.
Here is the structure you need to know before you buy anything.
You cannot touch the money for twelve months. Not a penalty, not a fee, you simply cannot get it
out. If there is any chance you need this money inside a year, stop here, this is not the account
for you.
If you cash out before five years you give up three months of interest. That is a mild penalty,
not a brutal one, and after five years it disappears entirely.
And there is an annual purchase cap per person, which is why this is a place to put some of your
savings and not a place to put all of it.
So say you park ten thousand dollars. The bond gives you a rate that tracks inflation and is
locked away for a year. The savings account gives you a rate that moves whenever the central bank
moves and that you can reach on a Tuesday afternoon.
That is the actual trade. It is not which one pays more this month. It is whether you are buying
a return or buying access, and you cannot buy both with the same dollar.
How I would think about it. Emergency money belongs in savings. All of it. The moment you put
your emergency fund somewhere with a twelve month lockup it is not an emergency fund anymore, it
is just money you feel good about.
Money you have earmarked for something two or three years out is where the bond gets interesting,
because the lockup is not a cost to you and the inflation adjustment is doing something a savings
account cannot do.
And if the composite rate has moved since you watched this, which it will have, go look it up
before you act on anything I just said. That is true of every number in this video.
I am not a financial advisor and this is not financial advice. If you are holding one of these
from the high rate era, check what it is paying you now, because it is almost certainly not what
it was paying then. See you next time.
"""


VIDEOS = [
    {
        "dir": "v1-401k-limits-explained",
        "video_id": "placeholder-v1-401k-limits",
        "title": "401(k) limits explained (and the 3 mistakes I see every year)",
        "published_at": "2024-02-14T15:00:00Z",
        "description": (
            "The contribution limit, what it actually covers, and the three mistakes that cost "
            "people the most money."
        ),
        "script": V1,
    },
    {
        "dir": "v2-mortgage-or-invest",
        "video_id": "placeholder-v2-mortgage-or-invest",
        "title": "Pay off your mortgage or invest? The honest answer",
        "published_at": "2025-06-18T14:00:00Z",
        "description": (
            "Guaranteed return versus expected return, and the three things the spreadsheet "
            "leaves out."
        ),
        "script": V2,
    },
    {
        "dir": "v3-hsa-triple-tax-advantage",
        "video_id": "placeholder-v3-hsa-triple-tax",
        "title": "HSA: the triple tax advantage almost nobody uses properly",
        "published_at": "2025-01-22T16:30:00Z",
        "description": (
            "The limits, the receipt trick that makes this account extraordinary, and the "
            "boundaries."
        ),
        "script": V3,
    },
    {
        "dir": "v4-how-id-invest-10000",
        "video_id": "placeholder-v4-invest-10000",
        "title": "How I'd invest $10,000 right now",
        "published_at": "2026-03-05T13:00:00Z",
        "description": (
            "Four steps, three funds, and every number in it is an illustration rather than a "
            "quote."
        ),
        "script": V4,
    },
    {
        "dir": "v5-ibonds-vs-high-yield-savings",
        "video_id": "placeholder-v5-ibonds-vs-savings",
        "title": "I-bonds vs high-yield savings: which one actually wins?",
        "published_at": "2024-11-12T17:00:00Z",
        "description": (
            "The composite rate, the twelve month lockup, and why the rate everybody remembers "
            "is gone."
        ),
        "script": V5,
    },
]


def script_to_subtitles(script: str) -> list[srt.Subtitle]:
    """Chunk a script into fixed-length cues with monotonic, non-overlapping timings."""
    words = script.split()
    subs: list[srt.Subtitle] = []
    cursor = 0.0
    for i in range(0, len(words), WORDS_PER_CUE):
        chunk = " ".join(words[i : i + WORDS_PER_CUE])
        subs.append(
            srt.Subtitle(
                index=len(subs) + 1,
                start=timedelta(seconds=round(cursor, 3)),
                end=timedelta(seconds=round(cursor + CUE_SECONDS, 3)),
                content=chunk,
            )
        )
        cursor += CUE_SECONDS + GAP_SECONDS
    return subs


def build() -> None:
    CHANNEL_DIR.mkdir(parents=True, exist_ok=True)
    for video in VIDEOS:
        folder = CHANNEL_DIR / video["dir"]
        folder.mkdir(parents=True, exist_ok=True)
        meta = {
            "video_id": video["video_id"],
            "title": video["title"],
            "published_at": video["published_at"],
            "description": video["description"],
        }
        (folder / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        subs = script_to_subtitles(video["script"])
        (folder / "captions.srt").write_text(srt.compose(subs))
        print(f"{video['dir']:34s} {len(video['script'].split()):4d} words  {len(subs):3d} cues")


if __name__ == "__main__":
    build()
