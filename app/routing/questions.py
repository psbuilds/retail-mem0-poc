"""Typed questions sent to Jev.

Question ids are app-chosen and are echoed back under the same keys, so the
classifier never depends on model-generated field names.  Each instruction is a
single yes/no statement (Noul) or a classification task (Choice).

These are the only questions the router asks; adding a capability means adding a
question here and a guard/threshold for it in ``classifier.py``.
"""

from __future__ import annotations

from typing import Any

# Noul questions: probability that the statement is true for the current message.
NOUL_QUESTIONS: dict[str, dict[str, Any]] = {
    "wants_products": {
        "type": "noul",
        "instructions": (
            "The customer is asking to see, find, compare, refine or be "
            "recommended products from the catalogue (for example 'show me "
            "earbuds under 5000' or 'cheaper ones'), not merely stating a "
            "long-term preference."
        ),
    },
    "refers_to_previous": {
        "type": "noul",
        "instructions": (
            "The message refers back to products or recommendations already "
            "made earlier in this conversation (for example 'cheaper ones', "
            "'which is better', 'tell me more about the second one')."
        ),
    },
    "states_preference": {
        "type": "noul",
        "instructions": (
            "The customer states a durable preference about brands, product "
            "types or features they generally like or dislike (for example "
            "'I usually prefer Sony headphones'). A one-off budget or gift "
            "request is not a durable preference."
        ),
    },
    "changes_memory": {
        "type": "noul",
        "instructions": (
            "The customer asks to correct, replace, withdraw or forget a "
            "previously saved preference (for example 'forget that I prefer "
            "Sony' or 'I no longer prefer Sony')."
        ),
    },
    "needs_personalisation": {
        "type": "noul",
        "instructions": (
            "A correct answer requires the customer's saved long-term "
            "preferences (for example 'recommend something based on what you "
            "know about me'). An explicit standalone request does not."
        ),
    },
}

INTENT_QUESTION: dict[str, Any] = {
    "type": "choice",
    "instructions": "What is the customer mainly asking for in this message?",
    "criteria": {
        "greeting": "A greeting or social opening with no request.",
        "general_question": "A question about concepts or products in general, not a catalogue request.",
        "product_search": "A request for products, prices or availability from the catalogue.",
        "product_followup": "A follow-up about products already recommended earlier in the conversation.",
        "preference_statement": "A statement about a durable personal preference, with no product request.",
        "memory_question": "A question about what the assistant remembers or has saved.",
        "memory_correction": "A request to correct, replace or forget a saved preference.",
    },
}

FOLLOWUP_QUESTION: dict[str, Any] = {
    "type": "choice",
    "instructions": (
        "If the message follows up on earlier recommendations, what does it "
        "want? Otherwise choose 'none'."
    ),
    "criteria": {
        "none": "Not a follow-up on earlier recommendations.",
        "refine_search": "Adjust the earlier search and show new products (cheaper, different brand, more options).",
        "compare": "Compare or choose between products already recommended.",
        "explain": "Explain or elaborate on products already recommended.",
    },
}


def build_questions() -> dict[str, Any]:
    """Return a fresh question map for one Jev request."""
    questions: dict[str, Any] = {}
    for question_id, question in NOUL_QUESTIONS.items():
        questions[question_id] = dict(question)
    questions["intent"] = dict(INTENT_QUESTION)
    questions["followup_action"] = dict(FOLLOWUP_QUESTION)
    return questions


NOUL_IDS: tuple[str, ...] = tuple(NOUL_QUESTIONS)
