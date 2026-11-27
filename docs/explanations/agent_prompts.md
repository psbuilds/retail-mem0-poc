# `app/agent/prompts.py`

## 1. File purpose

Holds every LLM prompt used by the workflow, plus small builders that inject
runtime context. Keeping prompts here makes wording reviewable and keeps nodes
focused on logic.

## 2. Imports and dependencies

No imports. The module produces plain strings only, so it has no dependency on
Ollama, LangGraph or Streamlit.

## 3. Classes

No classes.

## 4. Constants

### `REQUIREMENTS_SYSTEM_PROMPT`
Instructs the model to return only a JSON object with:
`is_shopping_request`, `refines_previous_request`, `category`, `brand`,
`max_budget`, `wireless`, `noise_cancellation`, `required_features`,
`excluded_features`.

Key rules:
- extract only the current message;
- previous requirements/memories are context **only** for the refinement flag;
- convert "3k"/"₹3,000" to a number;
- "ANC" means noise cancellation, "wired" means not wireless;
- never invent values;
- non-shopping messages get `is_shopping_request: false`.

### `CONVERSATION_SYSTEM_PROMPT`
Persona and rules for greetings/general questions: do not claim a catalogue
search, do not invent products or prices, invite a shopping request, stay under
90 words, and never claim a preference was saved.

### `COMPARISON_SYSTEM_PROMPT`
Rules for comparing/explaining **previously recommended** products only: use the
provided attributes, name concrete trade-offs, mention ₹ prices, stay under 160
words, never invent products, never claim a saved preference.

Preference extraction no longer has a prompt: Mem0 performs extraction from the
customer's sentence with `infer=true`.

### `RESPONSE_SYSTEM_PROMPT`
Sets the assistant persona and hard rules: recommend only provided products, never
invent products/prices/stock, explain fit, respect the current override, mention
relaxed preferences when present, format prices in ₹, stay under 180 words, and do
not claim a preference was saved.

## 5. Functions and methods

### `requirements_user_prompt(message, previous_requirements, conversation_history, customer_memories) -> str`
Builds the extraction prompt with:
- previous resolved requirements;
- the last 6 conversation turns;
- up to 10 memory sentences (or "(none retrieved)");
- the current message in `repr` form.

Returns a single string. It includes context so the model can set
`refines_previous_request` correctly, while the system prompt forbids copying
values from context.

### `conversation_user_prompt(message, conversation_history) -> str`
Includes the last 6 turns and the current message for a conversational reply.

### `comparison_user_prompt(message, recommendations, requirements=None, followup_action="compare") -> str`
Serialises the numbered earlier products (name, brand, price, rating, wireless,
ANC, features), the requested action and the earlier requirements.

### `response_user_prompt(message, requirements, memories, recommendations, memory_status, relaxed_preferences=None) -> str`
Serialises the selected products (name, brand, price, rating, wireless, ANC,
features), resolved requirements, memory status/texts and relaxed preferences.
The model receives only catalogue data, so it cannot legitimately invent a
product.

## 6. Execution workflow

```
understand_request -> requirements_user_prompt -> Ollama.chat_json
update_memory      -> preference_user_prompt  -> Ollama.chat_json
generate_response  -> response_user_prompt    -> Ollama.chat
```

## 7. Input and output

Input example:
```python
requirements_user_prompt(
    "Show me JBL headphones under ₹3,000 this time.",
    {"category": "headphones", "brand": "Sony"},
    [{"role": "user", "content": "Suggest headphones for me."}],
    ["Usually prefers Sony headphones with noise cancellation."],
)
```

Output (abridged):
```
Previous resolved requirements: {'category': 'headphones', 'brand': 'Sony'}
Recent conversation:
user: Suggest headphones for me.
Long-term customer preferences:
- Usually prefers Sony headphones with noise cancellation.
Current customer message: 'Show me JBL headphones under ₹3,000 this time.'
Return the JSON object now.
```

## 8. Important interaction

`nodes.py` imports the constants/builders. `llm/ollama_client.py` does **not**
import prompts; it only sends messages.

## 9. Important implementation decisions

- JSON mode is requested through the Ollama client, and output is still validated
  with Pydantic and grounded deterministically — prompts alone are not trusted.
- Context is truncated (6/4 turns, 10 memories) to keep prompts small and bounded.
- The response prompt receives no tools and no memory-write capability, so the LLM
  cannot claim it saved something.
- Prompts are plain Python strings rather than template files, keeping the POC
  small and readable.

## 10. Example execution

```python
from app.agent.prompts import RESPONSE_SYSTEM_PROMPT, response_user_prompt

messages = [
    {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
    {"role": "user", "content": response_user_prompt(
        "Suggest headphones", {"category": "headphones"}, [], [product], "success"
    )},
]
```
