"""
What your test runs. ← UNIT 8, MILESTONE 3

Each of your five criteria needs something run against it. A criterion about
false positives needs your own writing. One about label coverage needs inputs
across the score range. One about the appeal path needs a real content_id.

Working that out is Milestone 3's first step, and this file is where you write
it down. `run_eval.py` runs everything here three times.

Two are filled in as examples. Replace them with what your criteria actually
need — these are a shape, not a set.

A scenario carries **either** of these:

    "texts"   a list of strings. Each is sent as {"text": ..., "creator_id": ...}
              with a creator_id made up for you. Use this for most criteria.

    "bodies"  a list of whole request bodies, sent exactly as written. Use this
              when the malformed request IS the point — empty text, no `text`
              field at all, something enormous. A bad-input criterion needs it,
              because you cannot express "the field is missing" as a string.
"""

SCENARIOS = [
    {
        "name": "my own writing",
        "criterion": 1,
        # These are short, first-person pieces I wrote myself and use as the
        # false-positive check: a real writer should not keep getting hit as AI.
        "texts": [
            "I spent the morning sorting receipts and noticing how much of a normal week is just tiny admin tasks stacked on top of one another. The coffee had gone cold before I finally opened the email from my landlord, and I had to stop myself from treating it like a personal attack. It was a reminder that most of my stress is not dramatic at all, just repetitive and oddly cumulative.",
            "My favorite part of the weekend is the quiet hour when the apartment settles down and I can think without anyone asking for anything. I usually make a grocery list, read a couple of pages, and then drift into a to-do list that is half useful and half wishful thinking. It feels productive, even when I am mostly just checking in with myself.",
            "I walked to the corner store because I wanted to leave the house for a minute and not look at my screen for a while. The weather was cool enough that I kept my jacket on, and I noticed a bakery on the next block that I had never really paid attention to before. I bought tea and a pastry, then sat outside for twenty minutes pretending I had nowhere to be.",
            "The hardest part of learning a new skill is not the first lesson; it is the part where you realize how much repetition is required before the work starts to feel natural. I keep reminding myself that progress is usually boring and then invisible until one day it clicks. That is the part I have to trust even when I cannot see it yet.",
            "I was frustrated with myself for taking too long to answer a simple message, and then I realized I had been holding my attention in a dozen different places all morning. It was not a big crisis, just the kind of mental clutter that makes everything feel heavier than it is. I closed my tabs, took a breath, and started over with one task only.",
            "The best writing advice I have ever gotten is to write the sentence I actually mean, not the sentence I think sounds impressive. That rule saves me from overexplaining and from pretending my thoughts are cleaner than they are. A plain sentence that says what I mean is usually worth ten clever ones that hide the point.",
            "I still like to keep a handwritten list on my desk because it makes the work feel grounded in a way a digital list does not. I can circle things, cross out bad ideas, and watch a small plan become a real sequence of actions. It reminds me that productivity is just a rhythm, not a performance.",
            "Last night I cooked a simple dinner and left the pan on the stove a little too long because I had gotten distracted by a podcast. When I finally sat down to eat, I noticed that the meal was ordinary and exact in the best possible way. It felt like a good reminder that comfort can be unglamorous and still be enough.",
            "I am learning that not every problem needs a dramatic solution. Sometimes the right move is to keep the schedule loose, answer the email, and let the next small decision arrive when it actually matters. That approach does not feel exciting, but it is a lot easier to live with than constant urgency.",
            "I was proud of myself for leaving the meeting without taking on extra work that was not really mine. The conversation was fine, and my answer was honest, but it felt like a real boundary in the middle of a busy day. It is easy to confuse being useful with being available, and I am trying to separate those two things.",
        ],
    },
    {
        "name": "score spread",
        "criterion": 2,
        # These are chosen to sit across the score range, so the detector's average
        # separation shows up clearly across the three trials.
        "texts": [
            "In today\'s rapidly evolving digital environment, organizations are increasingly compelled to integrate advanced analytical systems that optimize operational efficiency while reducing manual overhead. Such initiatives often involve layered workflows, strategic resource allocation, and measurable performance indicators that support sustained adaptation across competitive markets.",
            "I kept my notes in the notebook by the window because that is where I always think most clearly. The page was messy, but it was my mess, and it made sense to me in a way the tidy version never did. I wrote the important parts in the margins and let the rest stay loose until I had a better idea.",
            "The project started with a few rough notes and a vague sense that the problem was bigger than the first draft suggested. I kept the language simple, checked the logic again, and tried to explain the tradeoffs without pretending the answer was perfectly clean. It was not elegant, but it was honest and that mattered more.",
        ],
    },
    {
        "name": "label coverage",
        "criterion": 3,
        "texts": [
            "I am trying to make this week less full of noise and more focused on the things that actually matter. I have been keeping my calendar honest, leaving some space open, and paying attention to which tasks feel urgent versus just loud. It is a small adjustment, but it has made the days feel less crowded.",
            "The briefing described several cross-functional priorities, including stakeholder alignment, iterative quality review, and measurable outcomes tied to operational continuity. The recommendations were framed in a balanced tone and emphasized practical next steps rather than speculative claims.",
            "The platform aggregates user behavior signals across multiple channels to produce consolidated summaries designed for rapid review and decision support. These outputs are generated through structured pipelines that prioritize consistency, interpretability, and efficiency for enterprise-scale analysis workloads.",
        ],
    },
    {
        "name": "appeal path",
        "criterion": 4,
        # This scenario is still submitted by run_eval.py, but the appeal itself must
        # be tested manually because the service needs a content_id returned from the
        # first POST before the appeal can be sent.
        "texts": [
            "I submitted a short example to check whether the appeal workflow records the disputed content and changes the status as expected. The first response gives me the content_id, and then I send a second request explaining why the original score should be revisited.",
            "The second submission is deliberately modest so the appeal trail is easy to read in the logs. It makes the workflow visible without forcing a large or noisy payload into the service.",
        ],
    },
    {
        "name": "bad input",
        "criterion": 5,
        "bodies": [
            {"text": "", "creator_id": "eval_bad_empty"},
            {"creator_id": "eval_bad_missing"},
            {"text": "word " * 50000, "creator_id": "eval_bad_huge"},
            {"text": None, "creator_id": "eval_bad_null"},
        ],
    },
]


def validate() -> list[str]:
    """
    Complain about anything malformed, before a long run rather than during.

    A "bodies" scenario is deliberately exempt from the empty check — an empty
    string is the whole point of a bad-input case, and refusing to run one
    would make that criterion untestable.
    """
    problems = []
    for i, scenario in enumerate(SCENARIOS, 1):
        if not scenario.get("name"):
            problems.append(f"scenario {i} has no name")

        texts = scenario.get("texts")
        bodies = scenario.get("bodies")

        if texts and bodies:
            problems.append(
                f"scenario {i} ('{scenario.get('name')}') has both texts and "
                f"bodies — pick one"
            )
            continue

        if bodies:
            for j, body in enumerate(bodies, 1):
                if not isinstance(body, dict):
                    problems.append(
                        f"scenario {i}, body {j} is not a dict — bodies are whole "
                        f"request bodies, like {{'text': '', 'creator_id': 'x'}}"
                    )
            continue

        if not texts:
            problems.append(f"scenario {i} has no texts and no bodies")
            continue

        for j, text in enumerate(texts, 1):
            if not str(text).strip():
                problems.append(
                    f"scenario {i}, text {j} is empty — if an empty submission is what "
                    f"you're testing, use 'bodies' instead of 'texts'"
                )
            elif str(text).startswith("REPLACE ME"):
                problems.append(
                    f"scenario {i} ('{scenario.get('name')}'), text {j} is still "
                    f"the placeholder"
                )
    return problems
