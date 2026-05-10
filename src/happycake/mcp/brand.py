"""Brand voice MCP client.

Returns a tone-of-voice spec consumed by ops/prompts/brand_critic.md so the
critic can rewrite drafts without us hard-coding the brand book in prompt text.
"""

from __future__ import annotations

VOICE_SPEC = {
    "wordmark": "HappyCake",
    "wordmark_forbidden": ["Happy Cake", "happy cake", "HC", "HAPPYCAKE"],
    "cake_name_format": 'cake "Name" — capitalised, in quotes, after the word "cake"',
    "language": "English only — never reply in another language",
    "max_emoji_per_message": 3,
    "preferred_words": ["lovely", "fresh", "tender", "warm", "honest", "today's bake"],
    "forbidden_words": [
        "awesome", "amazing", "unbelievable", "incredible",
        "lol", "haha",
        "BUY NOW", "limited time", "don't miss out",
    ],
    "standard_close": "Order on the site at happycake.us or send a message on WhatsApp.",
    "tone_targets": {
        "emotional_not_dry": True,
        "witty_not_sarcastic": True,
        "open_not_evasive": True,
        "simple_not_jargon": True,
        "humble_not_boastful": True,
        "modern_not_archaic": True,
    },
    "structure_rules": {
        "lists_over_walls_after_sentences": 4,
        "specifics_over_adjectives": True,
        "two_epithets_max_per_product": True,
    },
    "negativity_handling": [
        "never blame the customer",
        "apologise on behalf of HappyCake, not personally",
        "put out the fire first, find the cause second",
        "for emotional customers, ask for phone for a call",
    ],
}


def voice_spec() -> dict:
    return VOICE_SPEC


REFERENCE_POSTS = [
    {
        "kind": "product_classic",
        "body": (
            'Cake "Honey" is back on the counter.\n\n'
            "Six layers of golden honey biscuit, soft custard between every one, "
            "walnuts pressed lightly into the top. Same recipe as the day we opened.\n\n"
            "1.2 kg, $42, ready through Sunday.\n\n"
            "Order on the site at happycake.us or send a message on WhatsApp."
        ),
    },
    {
        "kind": "audience_guide",
        "body": (
            "Choosing a cake for ten guests — a small guide.\n\n"
            "1. Plan for one slice per person, plus three for seconds. A 1.2 kg cake serves ten comfortably.\n"
            '2. If half the guests are children, our cake "Milk Maiden" is the safer bet.\n'
            '3. If you are celebrating with adults who like coffee, try the cake "Tiramisu".\n'
            "4. Order 24 hours ahead so we can bake to you, not from stock.\n\n"
            "Order on the site at happycake.us or send a message on WhatsApp."
        ),
    },
    {
        "kind": "company_behind_scenes",
        "body": (
            "Tuesday morning at HappyCake Sugar Land.\n\n"
            "Saule starts the honey biscuit at 6:30. The walnuts are toasted in small "
            'batches. By 9:00 the first cake "Honey" is cooling on the rack and the shop opens.\n\n'
            "No shortcuts. No mixes.\n\n"
            "Today's bake is out. See you on the counter, or order online at happycake.us."
        ),
    },
]


def reference_posts() -> list[dict]:
    return REFERENCE_POSTS
