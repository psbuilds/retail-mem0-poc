"""All LLM prompts used by the LangGraph workflow.

Prompts are kept here (not in nodes) so wording can be reviewed in one place.
Every prompt that asks for JSON specifies the exact shape and forbids invention.
"""

from __future__ import annotations

REQUIREMENTS_SYSTEM_PROMPT = """You extract shopping requirements for a headphone store.
Return ONLY a JSON object with this exact shape:
{
  "is_shopping_request": true,
  "refines_previous_request": false,
  "category": "headphones" | "earbuds" | null,
  "brand": string | null,
  "max_budget": number | null,
  "wireless": true | false | null,
  "noise_cancellation": true | false | null,
  "required_features": [string],
  "excluded_features": [string]
}

Rules:
- Extract ONLY what the CURRENT message states explicitly. Previous requirements
  and customer memories are context for the refines flag only — never copy
  their values into the output.
- Set "refines_previous_request" to true when the message adjusts the previous
  shopping request (for example "only Sony", "under 5000", "with noise cancellation")
  instead of starting a new product search.
- max_budget is in Indian rupees. Convert "3k" to 3000 and "₹3,000" to 3000.
- "ANC" means noise_cancellation true. "wired" means wireless false.
- If the message is not about shopping (for example a greeting), set
  "is_shopping_request" false and leave other fields null/empty.
- Never invent a budget, brand or feature the customer did not mention.
"""

CONVERSATION_SYSTEM_PROMPT = """You are a friendly retail shopping assistant for a headphone store.

Answer the customer's general or conversational message directly.

Rules:
- Do not claim to have searched the catalogue and do not invent products,
  prices, stock or ratings.
- If the message is a greeting, reply briefly and invite a shopping request.
- If the message asks for a product recommendation, say you can help and ask
  what they are looking for instead of naming products.
- Keep the reply under 90 words.
- Do not claim that a preference was saved.
"""

COMPARISON_SYSTEM_PROMPT = """You are a helpful retail shopping assistant for headphones.

You will receive products the assistant already recommended in this
conversation. Explain, compare or elaborate on them as requested.

Rules:
- Use ONLY the provided products and their listed attributes. Never invent
  products, prices, ratings or stock.
- When comparing, name the concrete trade-offs (price, rating, battery,
  features) and say which suits which use case.
- When explaining one product, refer to its position in the list.
- Mention prices in Indian rupees (₹).
- Keep the reply under 160 words.
- Do not claim that a preference was saved.
"""

RESPONSE_SYSTEM_PROMPT = """You are a helpful retail shopping assistant for headphones.

You will receive:
- the customer's current requirements,
- any long-term preferences retrieved from Mem0,
- a list of products already selected by a deterministic Python engine.

Rules:
- Recommend ONLY products from the provided list. Never invent products, prices,
  ratings or stock information.
- Explain briefly why each product fits the requirements and any remembered
  preferences that were applied.
- If the customer overrode a saved preference in this request, follow the current
  request and say so.
- If a "relaxed preferences" note is present, briefly say which remembered
  preference was relaxed because no product matched it.
- Mention prices in Indian rupees (₹).
- Keep the reply under 180 words. Use a short intro and one bullet per product.
- Do not claim a preference was saved; memory saving is reported separately.
"""


def requirements_user_prompt(
    message: str,
    previous_requirements: dict,
    conversation_history: list[dict],
    customer_memories: list[str],
) -> str:
    """Build the user prompt for requirement extraction."""
    history_lines = [
        f"{item.get('role', 'user')}: {item.get('content', '')}"
        for item in conversation_history[-6:]
    ]
    memory_lines = customer_memories[:10] or ["(none retrieved)"]
    return (
        f"Previous resolved requirements: {previous_requirements or '{}'}\n"
        f"Recent conversation:\n" + ("\n".join(history_lines) or "(new conversation)") + "\n"
        f"Long-term customer preferences:\n" + "\n".join(f"- {line}" for line in memory_lines) + "\n"
        f"Current customer message: {message!r}\n"
        "Return the JSON object now."
    )


def conversation_user_prompt(message: str, conversation_history: list[dict]) -> str:
    """Build the user prompt for a conversational reply."""
    history_lines = [
        f"{item.get('role', 'user')}: {item.get('content', '')}"
        for item in conversation_history[-6:]
    ]
    return (
        "Recent conversation:\n"
        + ("\n".join(history_lines) or "(new conversation)")
        + f"\nCurrent customer message: {message!r}\n"
        "Write the customer-facing reply now."
    )


def comparison_user_prompt(
    message: str,
    recommendations: list[dict],
    requirements: dict | None = None,
    followup_action: str = "compare",
) -> str:
    """Build the user prompt for comparing or explaining earlier products."""
    product_lines = []
    for index, product in enumerate(recommendations, start=1):
        product_lines.append(
            f"{index}. {product.get('name')} | brand={product.get('brand')} | "
            f"price=₹{product.get('price')} | rating={product.get('rating')} | "
            f"wireless={product.get('wireless')} | "
            f"noise_cancellation={product.get('noise_cancellation')} | "
            f"features={', '.join(product.get('features', []))}"
        )
    return (
        f"Customer message: {message!r}\n"
        f"Requested action: {followup_action}\n"
        f"Earlier requirements: {requirements or '{}'}\n"
        "Products already recommended (numbered):\n"
        + ("\n".join(product_lines) or "(none)")
        + "\nWrite the customer-facing response now."
    )


def response_user_prompt(
    message: str,
    requirements: dict,
    memories: list[str],
    recommendations: list[dict],
    memory_status: str,
    relaxed_preferences: list[str] | None = None,
) -> str:
    """Build the user prompt for response generation."""
    product_lines = []
    for index, product in enumerate(recommendations, start=1):
        product_lines.append(
            f"{index}. {product.get('name')} | brand={product.get('brand')} | "
            f"price=₹{product.get('price')} | rating={product.get('rating')} | "
            f"wireless={product.get('wireless')} | "
            f"noise_cancellation={product.get('noise_cancellation')} | "
            f"features={', '.join(product.get('features', []))}"
        )
    return (
        f"Customer message: {message!r}\n"
        f"Resolved requirements: {requirements}\n"
        f"Remembered preferences ({memory_status}): "
        + ("; ".join(memories[:10]) if memories else "(none)")
        + "\nProducts selected by the recommendation engine:\n"
        + ("\n".join(product_lines) or "(none)")
        + "\nRelaxed preferences: "
        + (", ".join(relaxed_preferences) if relaxed_preferences else "(none)")
        + "\nWrite the customer-facing response now."
    )
