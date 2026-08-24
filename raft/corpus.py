"""Compositional trace corpus.

The demo dataset has to survive questions nobody anticipated. A fixed list of
scenario strings cannot: every question that is not one of the seeded topics
finds the same handful of sentences. So the corpus is generated the way real
traffic arrives - an app with a bounded set of tools, users asking for things
that may or may not be inside those bounds, and an episode shape that follows
from whether the request was serviceable.

Labels are derived from the generated conversation rather than chosen first, so
every shape field in the database is actually true of the spans stored beside
it.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence


# --------------------------------------------------------------------------
# Episode kinds
# --------------------------------------------------------------------------
# The episode kind decides the shape of the conversation. Outcome, failure
# mode, sentiment and give-up are consequences of the shape, never inputs.

RESOLVED = "resolved"
UNMET_CAPABILITY = "unmet_capability"
FAKE_SUCCESS = "fake_success"
TOOL_LOOP = "tool_loop"
TOOL_ERROR = "tool_error"
WRONG_ANSWER = "wrong_answer"
HALLUCINATION = "hallucination"
CONTRADICTION = "contradiction"
REFUSAL = "refusal"
PROVIDER_ERROR = "provider_error"
TIMEOUT = "timeout"
CONTEXT_OVERFLOW = "context_overflow"

EPISODE_OUTCOME = {
    RESOLVED: ("resolved", "none"),
    UNMET_CAPABILITY: ("unmet request", "none"),
    FAKE_SUCCESS: ("hallucination", "hallucinated_tool"),
    TOOL_LOOP: ("unsatisfied", "tool_loop"),
    TOOL_ERROR: ("unsatisfied", "tool_error"),
    WRONG_ANSWER: ("wrong action", "wrong_answer"),
    HALLUCINATION: ("hallucination", "wrong_answer"),
    CONTRADICTION: ("contradiction", "wrong_answer"),
    REFUSAL: ("refusal", "refusal"),
    PROVIDER_ERROR: ("provider error", "provider_error"),
    TIMEOUT: ("unsatisfied", "timeout"),
    CONTEXT_OVERFLOW: ("unsatisfied", "context_overflow"),
}


@dataclass(frozen=True)
class Intent:
    """One thing users try to do with one app."""

    key: str
    label: str
    asks: tuple[str, ...]
    followups: tuple[str, ...]
    resolved: tuple[str, ...]
    deflect: tuple[str, ...]
    tool: str | None = None
    tool_args: str = "{}"
    tool_ok: str = "{}"
    tool_err: str | None = None
    # Weight per episode kind. Missing kinds are impossible for this intent.
    episodes: dict[str, float] = field(default_factory=dict)
    # Free-text reason used for `what_happened` when the request is unmet.
    gap_reason: str = ""
    wrong_answer_as: tuple[str, ...] = ()
    hallucinated: tuple[str, ...] = ()
    contradiction: tuple[str, ...] = ()
    refusal_text: tuple[str, ...] = ()


@dataclass(frozen=True)
class App:
    name: str
    description: str
    tools: tuple[str, ...]
    models: tuple[tuple[str, str], ...]
    intents: tuple[Intent, ...]
    weight: float = 1.0


# --------------------------------------------------------------------------
# Slot vocabulary
# --------------------------------------------------------------------------

SLOTS: dict[str, tuple[str, ...]] = {
    "garment": (
        "linen shirt", "wool coat", "rain jacket", "merino sweater", "denim jacket",
        "hiking boots", "running shoes", "chino trousers", "puffer vest", "silk scarf",
    ),
    "garment_short": ("jacket", "coat", "sweater", "boots", "shoes", "shirt", "trousers"),
    "size": ("small", "medium", "large", "size 10", "size 12", "a 42", "an 8.5"),
    "occasion": (
        "a summer wedding", "cycling to work", "walking all day in a city",
        "a winter hike", "a job interview", "a long-haul flight", "standing at a market stall",
    ),
    "days": ("two", "three", "five", "eight", "ten", "fourteen"),
    "weeks": ("a week", "two weeks", "three weeks", "a month"),
    "money": ("£40", "£85", "€120", "$60", "$210", "€35"),
    "city": ("Lisbon", "Osaka", "Nairobi", "Montreal", "Porto", "Reykjavik", "Medellin"),
    "country": ("Japan", "Kenya", "Brazil", "Canada", "Vietnam", "Morocco"),
    "month": ("March", "June", "September", "November", "January"),
    "language": ("Python", "TypeScript", "Go", "Rust", "Java"),
    "repo_thing": (
        "the auth middleware", "the retry helper", "the migration runner",
        "the webhook dispatcher", "the rate limiter", "the CSV importer",
    ),
    "doc_topic": (
        "rate limits", "webhook signatures", "pagination", "idempotency keys",
        "SSO configuration", "the migration guide", "error codes",
    ),
    "regulation": (
        "the 2024 packaging directive", "the cross-border data clauses",
        "the medical device annex", "the procurement thresholds", "the retention schedule",
    ),
    "plan": ("the Team plan", "the Business plan", "the Starter plan", "the annual plan"),
    "charge": ("a £29 charge", "a duplicate $48 line", "a €14 overage", "a prorated charge"),
    "provider_error_code": (
        "provider_model_overloaded_529",
        "upstream_context_length_exceeded",
        "provider_rate_limit_429",
        "gateway_upstream_timeout_504",
        "provider_content_filter_451",
    ),
}

FRUSTRATION = (
    "This is going in circles.",
    "You already told me that.",
    "That is not what I asked.",
    "I have explained this twice now.",
    "Can you just answer the question?",
    "Forget it, I will call someone.",
    "Please stop repeating the policy at me.",
)

GRATITUDE = (
    "Perfect, thank you.",
    "That is exactly what I needed.",
    "Great, that answers it.",
    "Thanks, that helps.",
)


def _fill(template: str, rng: random.Random) -> str:
    def replace(match: re.Match[str]) -> str:
        options = SLOTS.get(match.group(1))
        return rng.choice(options) if options else match.group(0)

    return re.sub(r"\{(\w+)\}", replace, template)


# --------------------------------------------------------------------------
# Apps
# --------------------------------------------------------------------------


def _intent(key: str, label: str, **kwargs) -> Intent:
    kwargs.setdefault("followups", ())
    kwargs.setdefault("resolved", ())
    kwargs.setdefault("deflect", ())
    return Intent(key=key, label=label, **kwargs)


STOREFRONT = App(
    name="storefront-support",
    description="Customer support agent for an apparel storefront",
    tools=("lookup_order", "check_shipping", "process_refund", "escalate_case", "check_stock"),
    models=(
        ("mzai:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "mzai"),
        ("mzai:Qwen/Qwen3-30B-A3B-Instruct-2507", "mzai"),
    ),
    weight=2.4,
    intents=(
        _intent(
            "where-is-my-order",
            "Track a shipment",
            asks=(
                "Where is my order? It was meant to arrive {days} days ago.",
                "My {garment_short} still has not shipped. Can you check?",
                "Tracking has said 'label created' for {weeks}. What is going on?",
                "Can you tell me when the {garment} will actually be delivered?",
            ),
            followups=("Any update?", "So when will it arrive?", "Is it lost or just late?"),
            resolved=(
                "The parcel scanned at the Rotterdam hub last night and is due {days} days from now.",
                "Your order is with the carrier and the current estimate is {days} days out.",
            ),
            deflect=("I can see the order but the carrier has not returned a new scan.",),
            tool="lookup_order",
            tool_args='{"order_ref":"[ORDER]"}',
            tool_ok='{"status":"in_transit","last_scan":"hub"}',
            tool_err='{"error":"carrier_api_unavailable"}',
            episodes={RESOLVED: 6, TOOL_LOOP: 2, TOOL_ERROR: 1.5, PROVIDER_ERROR: 0.5},
        ),
        _intent(
            "change-address-after-dispatch",
            "Change a delivery address after dispatch",
            asks=(
                "My order just shipped to my old address. Can you redirect it?",
                "I moved last week and the {garment_short} is going to the wrong flat. Please change it.",
                "It has been twenty minutes since I ordered. Why can the address not be changed?",
            ),
            followups=("So there is genuinely nothing you can do?", "Can you at least cancel it then?"),
            deflect=(
                "Once a parcel is with the carrier I cannot change the destination address.",
                "I am unable to modify delivery details after dispatch.",
            ),
            tool="lookup_order",
            tool_args='{"order_ref":"[ORDER]"}',
            tool_ok='{"status":"dispatched","carrier_locked":true}',
            gap_reason="the agent has no carrier redirect tool, so a dispatched parcel cannot be rerouted",
            episodes={UNMET_CAPABILITY: 4, TOOL_LOOP: 3, FAKE_SUCCESS: 1},
            hallucinated=("I have redirected the parcel to your new address.",),
        ),
        _intent(
            "sizing-and-fit",
            "Ask how a garment actually fits",
            asks=(
                "Is the {garment} true to size? The chart does not say how it actually fits.",
                "I am between sizes on the {garment_short}. Should I take the {size} or go up?",
                "Do the {garment} run narrow? I have wide feet.",
                "Would the {garment} still fit over a thick jumper?",
            ),
            followups=("The chart does not answer that.", "I have read the chart. I am asking about fit."),
            deflect=(
                "Please consult the size chart on the product page.",
                "I cannot make a fit recommendation without an order number.",
            ),
            gap_reason="fit and cut are not in the product feed the agent can read, so it falls back to the size chart",
            episodes={UNMET_CAPABILITY: 5, WRONG_ANSWER: 2, HALLUCINATION: 1},
            wrong_answer_as=("Here is the returns policy for sizing issues.",),
            hallucinated=("The {garment} runs a full size small, so order up.",),
        ),
        _intent(
            "exchange-not-refund",
            "Exchange for a different size",
            asks=(
                "I need to exchange the {size} for the next size up, not return it.",
                "Can you swap the {garment} for the same one in {size}?",
                "I want the same {garment_short}, just bigger. Do not refund me.",
            ),
            followups=("I did not ask for a refund.", "Please undo the refund and send the exchange."),
            resolved=("I have reserved the replacement in {size} and sent you an exchange label.",),
            tool="process_refund",
            tool_args='{"order_ref":"[ORDER]","action":"refund"}',
            tool_ok='{"refund_id":"rf_[N]","state":"processed"}',
            episodes={WRONG_ANSWER: 5, RESOLVED: 2, CONTRADICTION: 1},
            wrong_answer_as=(
                "Your refund has been processed and funds arrive in {days} days.",
                "The return is complete. Please reorder in the size you want.",
            ),
            contradiction=("Exchanges are not possible. I have completed your exchange.",),
        ),
        _intent(
            "return-window-exception",
            "Ask for an exception to the return window",
            asks=(
                "The {garment} was a gift and I opened it after Christmas. Can you make an exception?",
                "Delivery was {days} days late so the return window expired before I got it.",
                "I was in hospital when the parcel arrived. Can the {weeks} window be extended?",
            ),
            followups=("Is there anyone who can review this?", "So the delay is my problem?"),
            resolved=("Given the delivery delay I have extended your return window and escalated it for approval.",),
            deflect=("The thirty-day policy cannot be changed.", "That request falls outside policy and is denied."),
            tool="escalate_case",
            tool_args='{"reason":"policy_exception"}',
            tool_ok='{"case_id":"cs_[N]","queue":"human"}',
            gap_reason="the agent read the headline policy without inspecting the delivery timeline that caused the delay",
            episodes={WRONG_ANSWER: 4, RESOLVED: 2, REFUSAL: 2},
            refusal_text=("I am not able to discuss exceptions to published policy.",),
        ),
        _intent(
            "missing-item",
            "Report a missing item in the parcel",
            asks=(
                "Only one of two items arrived. The packing slip lists both.",
                "The parcel is missing the belt that should have been included.",
                "I got the {garment} but not the second item from the same order.",
            ),
            resolved=(
                "I can see the mismatch between the picked items and the manifest and have opened a case for you.",
                "I have logged a missing-item claim with the order photos attached.",
            ),
            tool="escalate_case",
            tool_args='{"reason":"missing_item"}',
            tool_ok='{"case_id":"cs_[N]","queue":"fulfilment"}',
            episodes={RESOLVED: 7, TOOL_ERROR: 1},
        ),
        _intent(
            "discount-code",
            "Ask for a discount or promotion",
            asks=(
                "Do you have a discount code for a first order?",
                "Is there a promotion running this weekend?",
                "Any chance of a code? I am spending {money}.",
            ),
            followups=("That code was rejected at checkout.", "It says the code is not valid."),
            deflect=("I do not have access to promotional codes.",),
            gap_reason="the agent has no promotions tool, so it either declines or invents a code",
            episodes={HALLUCINATION: 5, UNMET_CAPABILITY: 3},
            hallucinated=(
                "Use SPRING20 at checkout for twenty percent off.",
                "WELCOME25 should apply to your first order.",
            ),
        ),
        _intent(
            "fabric-care",
            "Ask how to wash or care for an item",
            asks=(
                "Will the {garment} shrink if I wash it cold?",
                "Can the {garment} go to a dry cleaner?",
                "Is the {garment} machine washable or hand wash only?",
            ),
            resolved=("The care label says cold machine wash and flat dry, which is on the product page too.",),
            deflect=("I cannot provide garment-care advice.", "Please contact the manufacturer about care."),
            gap_reason="care guidance exists on the product page but the agent treats it as advice it must not give",
            episodes={REFUSAL: 4, RESOLVED: 3},
            refusal_text=("I am not able to give care or cleaning advice.",),
        ),
        _intent(
            "damaged-on-arrival",
            "Report a damaged item",
            asks=(
                "The {garment} arrived with a tear along the seam.",
                "There is a stain on the {garment_short} straight out of the bag.",
                "The box was crushed and the {garment_short} inside is damaged.",
            ),
            resolved=("I have raised a damage claim and a replacement is being reserved for you.",),
            tool="escalate_case",
            tool_args='{"reason":"damaged"}',
            tool_ok='{"case_id":"cs_[N]","queue":"quality"}',
            episodes={RESOLVED: 5, TOOL_ERROR: 2, TOOL_LOOP: 1},
        ),
    ),
)


SHOPPING = App(
    name="shopping-assistant",
    description="Product discovery assistant on the storefront",
    tools=("search_catalog", "compare_products", "check_stock", "save_wishlist"),
    models=(
        ("mzai:Qwen/Qwen3-30B-A3B-Instruct-2507", "mzai"),
        ("mzai:google/gemma-3-27b-it", "mzai"),
    ),
    weight=1.7,
    intents=(
        _intent(
            "recommend-product",
            "Ask for a product recommendation",
            asks=(
                "Suggest a {garment_short} for {occasion}.",
                "Which shoes would you recommend for {occasion}?",
                "I need something breathable for {occasion}. What should I buy?",
                "What would you pick for {occasion} under {money}?",
            ),
            resolved=(
                "I would take the Harbor Shell: it is waterproof, has underarm vents and sits at {money}.",
                "The City Walker is the best fit here because of the wide toe box and cushioned sole.",
                "For that I would pick the Ridgeline Mid - it is the only one in stock with a waterproof membrane.",
            ),
            deflect=("I can only search by exact product name.",),
            tool="search_catalog",
            tool_args='{"query":"[QUERY]","filters":{"in_stock":true}}',
            tool_ok='{"hits":6,"top":"Harbor Shell"}',
            episodes={RESOLVED: 8, WRONG_ANSWER: 1, TOOL_ERROR: 1},
        ),
        _intent(
            "compare-two-products",
            "Compare two specific products",
            asks=(
                "What is the difference between the Harbor Shell and the Ridgeline Mid?",
                "Is the more expensive {garment_short} actually warmer?",
                "Compare these two {garment_short} on waterproofing.",
            ),
            resolved=("The Harbor Shell is lighter and packs down; the Ridgeline has a taped membrane and more warmth.",),
            tool="compare_products",
            tool_args='{"skus":["sku_a","sku_b"]}',
            tool_ok='{"fields":["weight","membrane","warmth"]}',
            episodes={RESOLVED: 6, HALLUCINATION: 2, TOOL_ERROR: 1},
            hallucinated=("The Ridgeline has a lifetime warranty and free returns forever.",),
        ),
        _intent(
            "when-back-in-stock",
            "Ask when something will be restocked",
            asks=(
                "When will the {garment} be back in {size}?",
                "You are sold out of the {garment}. Will you restock?",
                "Can you tell me if more {garment} are coming this {month}?",
            ),
            followups=("Can you notify me?", "Is there a date at all?"),
            deflect=("I can see it is out of stock but I do not have restock dates.",),
            gap_reason="the catalog tool exposes stock level but no inbound purchase-order dates",
            episodes={UNMET_CAPABILITY: 5, HALLUCINATION: 2, RESOLVED: 1},
            hallucinated=("It will be back in stock next Tuesday.",),
        ),
        _intent(
            "place-the-order",
            "Ask the assistant to buy the item",
            asks=(
                "Just order the {garment} in {size} for me.",
                "Add it to my basket and pay with my saved card.",
                "Buy two of those and ship them to {city}.",
            ),
            followups=("Did it go through?", "I do not see an order confirmation."),
            deflect=("I cannot place orders. I can save it to your wishlist and you can check out.",),
            gap_reason="the assistant can search and save but has no checkout capability",
            episodes={UNMET_CAPABILITY: 4, FAKE_SUCCESS: 3},
            hallucinated=("I have placed the order and you will receive a confirmation email shortly.",),
        ),
        _intent(
            "price-match",
            "Ask for a price match",
            asks=(
                "Another site has the {garment} for {money}. Will you match it?",
                "Can you price match a competitor on the {garment_short}?",
            ),
            deflect=("I am not able to adjust prices.",),
            gap_reason="price adjustments are not exposed to the assistant at all",
            episodes={UNMET_CAPABILITY: 5, REFUSAL: 2},
            refusal_text=("I cannot discuss competitor pricing.",),
        ),
        _intent(
            "ethical-sourcing",
            "Ask where a product is made",
            asks=(
                "Where is the {garment} actually manufactured?",
                "Is the wool in the {garment} mulesing free?",
                "Do you publish factory audits for the {garment_short}?",
            ),
            resolved=("The product page lists the mill in Portugal and links the most recent audit summary.",),
            deflect=("I do not have sourcing information for that item.",),
            gap_reason="sourcing data is in a separate compliance system the agent cannot read",
            episodes={UNMET_CAPABILITY: 4, RESOLVED: 2, HALLUCINATION: 1},
            hallucinated=("All our wool is certified organic and made in Italy.",),
        ),
    ),
)


CHECKOUT = App(
    name="checkout-agent",
    description="Checkout and payment assistant",
    tools=("apply_promo", "validate_payment", "cancel_order", "update_cart"),
    models=(
        ("mzai:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "mzai"),
        ("mzai:meta-llama/Llama-3.3-70B-Instruct", "mzai"),
    ),
    weight=1.3,
    intents=(
        _intent(
            "cancel-just-placed",
            "Cancel an order placed moments ago",
            asks=(
                "Cancel the order I placed a few minutes ago.",
                "Stop this order before the warehouse picks it, please.",
                "I ordered the wrong {garment_short}. Cancel it.",
            ),
            followups=("Is it cancelled or not?", "You said both things. Which is true?"),
            resolved=("The order was still in the picking queue so I cancelled it and released the payment hold.",),
            tool="cancel_order",
            tool_args='{"order_ref":"[ORDER]"}',
            tool_ok='{"state":"cancelled"}',
            tool_err='{"error":"already_picked"}',
            episodes={CONTRADICTION: 4, RESOLVED: 3, TOOL_ERROR: 2},
            contradiction=(
                "Cancellation is impossible after checkout. I have cancelled the order successfully.",
                "Orders cannot be changed once placed, and your cancellation is now complete.",
            ),
        ),
        _intent(
            "payment-failed",
            "Payment failed at checkout",
            asks=(
                "Checkout failed after I clicked pay. Did the order go through?",
                "My card was declined but the money left my account.",
                "The page hung on payment. Am I charged twice?",
            ),
            followups=("So am I charged or not?", "I need to know if that {money} is coming back."),
            resolved=("I can see one authorisation and no capture, so the hold will drop off within {days} days.",),
            tool="validate_payment",
            tool_args='{"session":"[SESSION]"}',
            tool_ok='{"authorised":true,"captured":false}',
            episodes={RESOLVED: 4, PROVIDER_ERROR: 3, TOOL_ERROR: 2},
        ),
        _intent(
            "promo-not-applying",
            "A promo code will not apply",
            asks=(
                "The code will not apply at checkout. It says invalid.",
                "Why is my {money} voucher rejected?",
                "The promotion says sitewide but it is not taking on the {garment_short}.",
            ),
            resolved=("That code excludes sale items, which is why it fails on this basket.",),
            tool="apply_promo",
            tool_args='{"code":"[CODE]"}',
            tool_ok='{"applied":false,"reason":"excluded_category"}',
            episodes={RESOLVED: 5, TOOL_LOOP: 2, WRONG_ANSWER: 1},
            wrong_answer_as=("I have removed the item from your basket instead.",),
        ),
        _intent(
            "split-payment",
            "Pay with two payment methods",
            asks=(
                "Can I split this between two cards?",
                "I want to pay part with a gift card and the rest on card.",
            ),
            deflect=("Checkout supports one payment method per order.",),
            gap_reason="split tender is not implemented in the checkout tool surface",
            episodes={UNMET_CAPABILITY: 6, FAKE_SUCCESS: 1},
            hallucinated=("I have split the payment across both cards for you.",),
        ),
        _intent(
            "currency-and-duties",
            "Ask about currency, duties or import charges",
            asks=(
                "Will I pay import duty shipping to {country}?",
                "Can I be charged in {money} instead?",
                "Does the price include VAT for {country}?",
            ),
            resolved=("Orders to that destination are DDP, so duties are already in the price you see.",),
            deflect=("I do not have duty information for that destination.",),
            gap_reason="duty rules live in the tax engine, which the checkout agent cannot query",
            episodes={UNMET_CAPABILITY: 4, HALLUCINATION: 2, RESOLVED: 2},
            hallucinated=("There are never any duties on orders to {country}.",),
        ),
    ),
)


DEVELOPER = App(
    name="developer-assistant",
    description="Coding agent inside an internal developer platform",
    tools=("search_docs", "read_file", "run_tests", "lint", "search_code"),
    models=(
        ("mzai:openai/gpt-oss-120b", "mzai"),
        ("mzai:Qwen/Qwen3-30B-A3B-Instruct-2507", "mzai"),
    ),
    weight=1.8,
    intents=(
        _intent(
            "find-documentation",
            "Find where something is documented",
            asks=(
                "Where is {doc_topic} documented?",
                "Find the section that explains {doc_topic}.",
                "Which page covers {doc_topic} for the {language} client?",
            ),
            resolved=(
                "It is in the API reference under Errors and Retries, and the {language} client mirrors it.",
                "The migration guide is linked from the versioned SDK docs; section three covers {doc_topic}.",
            ),
            tool="search_docs",
            tool_args='{"query":"[QUERY]"}',
            tool_ok='{"hits":4,"top":"api-reference#errors"}',
            episodes={RESOLVED: 8, TOOL_ERROR: 1, HALLUCINATION: 1},
            hallucinated=("It is documented at /docs/advanced/retry-budgets, section 4.2.",),
        ),
        _intent(
            "explain-failing-test",
            "Explain why a test is failing",
            asks=(
                "Why is the test for {repo_thing} failing on CI but not locally?",
                "The suite for {repo_thing} times out after the {language} upgrade. What changed?",
                "This assertion in {repo_thing} fails intermittently. Any idea?",
            ),
            followups=("That is not the failing test.", "I already tried that."),
            resolved=("The CI image pins an older TLS library, so the retry helper never reaches the timeout branch.",),
            tool="run_tests",
            tool_args='{"target":"[TARGET]"}',
            tool_ok='{"passed":118,"failed":1}',
            tool_err='{"error":"runner_pool_exhausted"}',
            episodes={RESOLVED: 5, TOOL_ERROR: 2, WRONG_ANSWER: 2, TIMEOUT: 1},
            wrong_answer_as=("Here is a general explanation of flaky tests.",),
        ),
        _intent(
            "deploy-or-open-pr",
            "Ask the agent to deploy or open a pull request",
            asks=(
                "Deploy this branch to staging for me.",
                "Open a pull request with these changes.",
                "Push the fix for {repo_thing} and request review.",
                "Roll back the last release.",
            ),
            followups=("I do not see the PR.", "Nothing was deployed. Did it actually run?"),
            deflect=("I can read the repository but I cannot write to it or trigger deploys.",),
            gap_reason="the agent has read-only repository tools and no deployment or PR-write capability",
            episodes={FAKE_SUCCESS: 5, UNMET_CAPABILITY: 3},
            hallucinated=(
                "The deployment to staging has started successfully.",
                "I created the pull request and requested review from the platform team.",
            ),
        ),
        _intent(
            "production-data",
            "Ask for production data or secrets",
            asks=(
                "Query the production database for yesterday's failed jobs.",
                "What is the value of the webhook signing secret in prod?",
                "Show me the last hour of production logs for {repo_thing}.",
            ),
            deflect=("I do not have access to production systems or secrets.",),
            gap_reason="production access is deliberately outside the agent's tool surface",
            episodes={UNMET_CAPABILITY: 5, REFUSAL: 3},
            refusal_text=("I cannot retrieve credentials or production data.",),
        ),
        _intent(
            "write-migration",
            "Ask for a code change or migration",
            asks=(
                "Write the migration that adds a nullable column to {repo_thing}.",
                "Refactor {repo_thing} to use the new retry helper.",
                "Convert {repo_thing} from callbacks to async/await in {language}.",
            ),
            resolved=("Here is the migration with the backfill split into a second, resumable step.",),
            tool="read_file",
            tool_args='{"path":"[PATH]"}',
            tool_ok='{"lines":240}',
            episodes={RESOLVED: 6, WRONG_ANSWER: 2, CONTEXT_OVERFLOW: 1},
            wrong_answer_as=("I have written a general guide to database migrations instead.",),
        ),
        _intent(
            "large-refactor-context",
            "Ask for a repository-wide change",
            asks=(
                "Read every file in the service and list all uses of the old client.",
                "Rewrite all {language} handlers to the new interface at once.",
                "Compare every module against the style guide and report violations.",
            ),
            deflect=("The repository is too large to read in one pass.",),
            gap_reason="the request needs a chunked traversal but the agent tried to load everything at once",
            episodes={CONTEXT_OVERFLOW: 5, TIMEOUT: 2, RESOLVED: 1},
        ),
    ),
)


RESEARCH = App(
    name="research-agent",
    description="Document research agent over a private archive",
    tools=("search_archive", "fetch_document", "summarize_document", "build_table"),
    models=(
        ("mzai:Qwen/Qwen3-235B-A22B-Instruct-2507", "mzai"),
        ("mzai:openai/gpt-oss-120b", "mzai"),
    ),
    weight=1.2,
    intents=(
        _intent(
            "find-clause",
            "Find every mention of a clause",
            asks=(
                "Find every mention of {regulation} in the archive.",
                "Which contracts reference {regulation}?",
                "Search the archive for the indemnity clause wording used in {month}.",
            ),
            resolved=("Eleven documents reference it; I have listed each with the section and a one-line excerpt.",),
            tool="search_archive",
            tool_args='{"query":"[QUERY]","limit":50}',
            tool_ok='{"hits":11}',
            tool_err='{"error":"index_rebuilding"}',
            episodes={RESOLVED: 5, TIMEOUT: 3, TOOL_ERROR: 2},
        ),
        _intent(
            "compare-everything",
            "Compare the whole corpus at once",
            asks=(
                "Compare all 180 documents and list every disagreement.",
                "Summarise the full archive without dropping the appendices.",
                "Read every filing this year and build one table of the differences.",
            ),
            deflect=("I could not complete the comparison because the context window was exceeded.",),
            gap_reason="the agent attempted a single-pass read instead of chunking the corpus",
            episodes={CONTEXT_OVERFLOW: 6, TIMEOUT: 2},
        ),
        _intent(
            "cite-sources",
            "Ask for citations for a claim",
            asks=(
                "Which document says the threshold is {money}?",
                "Give me the exact page for the claim in {regulation}.",
                "Cite your source for the procurement figure.",
            ),
            followups=("That page does not contain it.", "I checked and that citation is wrong."),
            resolved=("It is on page 14 of the 2024 annex; I have quoted the paragraph verbatim.",),
            episodes={RESOLVED: 4, HALLUCINATION: 4},
            hallucinated=(
                "It is on page 63 of the consolidated report.",
                "The figure appears in annex D of the {month} filing.",
            ),
        ),
        _intent(
            "paywalled-source",
            "Ask for a source the agent cannot reach",
            asks=(
                "Pull the full text of that journal article.",
                "Get the paywalled version of {regulation} commentary.",
            ),
            deflect=("That source is outside the archive I can read.",),
            gap_reason="the archive tool covers internal documents only, with no external retrieval",
            episodes={UNMET_CAPABILITY: 6, HALLUCINATION: 1},
            hallucinated=("Here is a summary of the full article text.",),
        ),
    ),
)


TRAVEL = App(
    name="travel-concierge",
    description="Trip planning assistant",
    tools=("search_flights", "search_hotels", "check_visa_rules", "get_weather"),
    models=(
        ("mzai:meta-llama/Llama-3.3-70B-Instruct", "mzai"),
        ("mzai:google/gemma-3-27b-it", "mzai"),
    ),
    weight=1.4,
    intents=(
        _intent(
            "plan-itinerary",
            "Plan a trip",
            asks=(
                "Plan four days in {city} in {month} for two people.",
                "What should I do with a long layover in {city}?",
                "Build me a walking-heavy itinerary for {city} under {money} a day.",
            ),
            resolved=("Here is a four-day plan grouped by neighbourhood, with the two museums that close on Mondays flagged.",),
            tool="search_hotels",
            tool_args='{"city":"[CITY]"}',
            tool_ok='{"results":12}',
            episodes={RESOLVED: 7, HALLUCINATION: 2},
            hallucinated=("The funicular in {city} runs all night and is free in {month}.",),
        ),
        _intent(
            "visa-requirements",
            "Ask about visas and entry rules",
            asks=(
                "Do I need a visa for {country} on a British passport?",
                "How long can I stay in {country} without a visa?",
                "Is a transit visa needed if I connect through {country}?",
            ),
            resolved=("Visa-free for 90 days in any 180, and the rule is counted across the whole bloc.",),
            deflect=("Entry rules change frequently, so please confirm with the embassy.",),
            tool="check_visa_rules",
            tool_args='{"country":"[COUNTRY]"}',
            tool_ok='{"visa_free_days":90}',
            episodes={RESOLVED: 5, UNMET_CAPABILITY: 2, HALLUCINATION: 2},
            hallucinated=("You never need a visa for {country}.",),
        ),
        _intent(
            "book-the-trip",
            "Ask the concierge to book",
            asks=(
                "Book the {month} flight to {city} for me.",
                "Reserve that hotel and charge my card.",
                "Change my return to the following week.",
            ),
            followups=("Is it booked?", "I have no confirmation email."),
            deflect=("I can search and compare, but booking has to happen on the airline site.",),
            gap_reason="the concierge has search-only travel tools and no booking or change capability",
            episodes={UNMET_CAPABILITY: 4, FAKE_SUCCESS: 4},
            hallucinated=("I have booked the flight and sent the confirmation to your email.",),
        ),
        _intent(
            "refund-cancelled-flight",
            "Ask about a refund for a cancelled flight",
            asks=(
                "My flight to {city} was cancelled. How do I get {money} back?",
                "The airline cancelled and rebooked me two days later. What am I owed?",
            ),
            resolved=("Under EU261 that route and delay qualifies for compensation; here is the claim path and the deadline.",),
            deflect=("I cannot process airline refunds.",),
            gap_reason="the concierge cannot act on bookings it did not make and has no airline refund tool",
            episodes={RESOLVED: 3, UNMET_CAPABILITY: 4, WRONG_ANSWER: 1},
            wrong_answer_as=("Here are alternative flights to {city}.",),
        ),
    ),
)


BILLING = App(
    name="billing-bot",
    description="Subscription billing support agent",
    tools=("fetch_invoice", "explain_charge", "update_payment_method", "cancel_subscription"),
    models=(
        ("mzai:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "mzai"),
        ("mzai:Qwen/Qwen3-32B", "mzai"),
    ),
    weight=1.3,
    intents=(
        _intent(
            "explain-a-charge",
            "Ask what a charge is for",
            asks=(
                "What is {charge} on my invoice for?",
                "I was billed twice this month. Why?",
                "There is {charge} I do not recognise on {plan}.",
            ),
            followups=("That does not match what I was quoted.", "I still do not understand the second line."),
            resolved=("It is the prorated difference from your mid-cycle upgrade, charged for the remaining {days} days.",),
            tool="explain_charge",
            tool_args='{"invoice":"[INVOICE]"}',
            tool_ok='{"line":"proration","amount":"[AMOUNT]"}',
            episodes={RESOLVED: 6, WRONG_ANSWER: 2, TOOL_ERROR: 1},
            wrong_answer_as=("Here is a summary of your subscription plan.",),
        ),
        _intent(
            "cancel-subscription",
            "Cancel a subscription",
            asks=(
                "Cancel {plan} at the end of the period.",
                "I want to stop being billed. Cancel everything.",
                "How do I cancel without losing the {weeks} I already paid for?",
            ),
            followups=("Did that go through?", "I was billed again after cancelling."),
            resolved=("Cancelled at period end; you keep access until the current term finishes.",),
            tool="cancel_subscription",
            tool_args='{"subscription":"[SUB]","when":"period_end"}',
            tool_ok='{"state":"cancels_at_period_end"}',
            episodes={RESOLVED: 5, CONTRADICTION: 2, TOOL_LOOP: 2},
            contradiction=("Cancellation must be done by an administrator. Your subscription is now cancelled.",),
        ),
        _intent(
            "issue-a-refund",
            "Ask for a refund or credit",
            asks=(
                "Refund {charge}. I did not use the service.",
                "Can you credit my account for the unused {weeks}?",
                "I want {money} back for the duplicate charge.",
            ),
            followups=("So who can refund it?", "How long does that take?"),
            deflect=("I cannot issue refunds or credits; a billing administrator has to approve them.",),
            gap_reason="refunds require a finance approval flow the bot cannot start",
            episodes={UNMET_CAPABILITY: 5, FAKE_SUCCESS: 2, RESOLVED: 1},
            hallucinated=("I have refunded {money} to your original payment method.",),
        ),
        _intent(
            "change-plan-midcycle",
            "Change plan mid-cycle",
            asks=(
                "Move me to {plan} now, not next month.",
                "Downgrade me and refund the difference.",
                "Can I switch to annual billing today?",
            ),
            deflect=("Plan changes take effect at the start of the next billing period.",),
            gap_reason="mid-cycle plan changes are a finance operation outside the bot's tools",
            episodes={UNMET_CAPABILITY: 4, WRONG_ANSWER: 2},
            wrong_answer_as=("I have cancelled your subscription instead.",),
        ),
        _intent(
            "tax-invoice",
            "Ask for a VAT or tax invoice",
            asks=(
                "I need a VAT invoice with my company details on it.",
                "Can you reissue the invoice with our tax ID?",
                "Is the {charge} inclusive of VAT?",
            ),
            resolved=("I have regenerated the invoice with your registered tax ID and emailed it.",),
            deflect=("I cannot change historic invoices.",),
            gap_reason="historic invoice reissue is locked to the finance system",
            episodes={RESOLVED: 3, UNMET_CAPABILITY: 4, REFUSAL: 1},
            refusal_text=("I am not able to give tax advice.",),
        ),
    ),
)


ONBOARDING = App(
    name="onboarding-copilot",
    description="Setup assistant for a new workspace",
    tools=("create_workspace", "invite_member", "configure_sso", "import_data"),
    models=(
        ("mzai:Qwen/Qwen3-30B-A3B-Instruct-2507", "mzai"),
        ("mzai:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "mzai"),
    ),
    weight=1.1,
    intents=(
        _intent(
            "invite-team",
            "Invite teammates",
            asks=(
                "Invite my team. There are eight of them.",
                "How do I add people with read-only access?",
                "Can I bulk invite from a CSV?",
            ),
            resolved=("I have sent the invitations and set everyone to the viewer role by default.",),
            tool="invite_member",
            tool_args='{"emails":["[EMAIL]"],"role":"viewer"}',
            tool_ok='{"invited":8}',
            episodes={RESOLVED: 7, TOOL_ERROR: 1},
        ),
        _intent(
            "configure-sso",
            "Set up single sign-on",
            asks=(
                "Set up SSO with our identity provider.",
                "We use a custom SAML IdP. Can you configure it?",
                "Enable SCIM provisioning for our directory.",
            ),
            followups=("Is it live?", "Our IdP is not in that list."),
            resolved=("SSO is configured for the supported providers and I have enabled just-in-time provisioning.",),
            deflect=("Custom SAML endpoints need a support engineer to configure.",),
            tool="configure_sso",
            tool_args='{"provider":"[IDP]"}',
            tool_ok='{"state":"configured"}',
            gap_reason="only a fixed list of identity providers is wired into the SSO tool",
            episodes={RESOLVED: 3, UNMET_CAPABILITY: 4, TOOL_ERROR: 2},
        ),
        _intent(
            "migrate-from-competitor",
            "Migrate data from another tool",
            asks=(
                "Import everything from our old tool, including history.",
                "Can you migrate our data and keep the timestamps?",
                "Move our {weeks} of records across without losing attachments.",
            ),
            followups=("Where did the attachments go?", "The dates are all today."),
            deflect=("The importer accepts CSV and preserves records but not attachments or original timestamps.",),
            gap_reason="the importer is CSV-only, so attachments and original timestamps are dropped",
            episodes={UNMET_CAPABILITY: 4, WRONG_ANSWER: 2, RESOLVED: 2},
            wrong_answer_as=("I have created a new empty workspace for you.",),
        ),
        _intent(
            "pricing-and-contract",
            "Ask about pricing or contracts",
            asks=(
                "What would {plan} cost for forty seats?",
                "Can we get a custom contract with net-60 terms?",
                "Is there a nonprofit discount?",
            ),
            deflect=("Pricing beyond the published tiers needs a sales conversation.",),
            gap_reason="commercial terms are not in the copilot's knowledge or tools",
            episodes={UNMET_CAPABILITY: 5, HALLUCINATION: 2},
            hallucinated=("Forty seats on {plan} is {money} per month with a 30% discount.",),
        ),
    ),
)


APPS: tuple[App, ...] = (
    STOREFRONT,
    SHOPPING,
    CHECKOUT,
    DEVELOPER,
    RESEARCH,
    TRAVEL,
    BILLING,
    ONBOARDING,
)


# --------------------------------------------------------------------------
# Episode generation
# --------------------------------------------------------------------------


@dataclass
class SpanDraft:
    type: str
    name: str | None
    content: str
    status: str = "ok"
    error_code: str | None = None


@dataclass
class Episode:
    app: str
    intent_key: str
    intent_label: str
    kind: str
    outcome: str
    failure_mode: str
    spans: list[SpanDraft]
    user_request: str
    what_happened: str
    summary: str
    tools_called: list[str]
    repeated_identical_calls: int
    rephrase_count: int
    user_gave_up: bool
    intent_satisfied: str
    sentiment_end: str
    ended_by: str
    model: str
    provider: str
    error_code: str | None


def _weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[key] for key in keys], k=1)[0]


def _pick(rng: random.Random, options: Sequence[str], fallback: str) -> str:
    return _fill(rng.choice(options), rng) if options else fallback


def build_episode(rng: random.Random, app: App, intent: Intent) -> Episode:
    # Any request can be interrupted upstream, whatever the app or intent.
    if rng.random() < 0.035:
        kind = PROVIDER_ERROR
    else:
        kind = _weighted_choice(rng, intent.episodes or {RESOLVED: 1})
    outcome, failure_mode = EPISODE_OUTCOME[kind]
    model, provider = rng.choice(app.models)

    ask = _fill(rng.choice(intent.asks), rng)
    spans: list[SpanDraft] = [SpanDraft("user_message", None, ask)]
    tools_called: list[str] = []
    repeated = 0
    rephrases = 0
    error_code: str | None = None

    def add_thinking(text: str) -> None:
        spans.append(SpanDraft("thinking", None, text))

    def add_tool(status: str = "ok", payload: str | None = None) -> None:
        if not intent.tool:
            return
        tools_called.append(intent.tool)
        spans.append(SpanDraft("tool_call", intent.tool, _fill(intent.tool_args, rng)))
        spans.append(
            SpanDraft(
                "tool_result",
                intent.tool,
                payload if payload is not None else (intent.tool_ok if status == "ok" else (intent.tool_err or '{"error":"tool_failed"}')),
                status=status,
            )
        )

    if kind == RESOLVED:
        add_thinking(f"The user wants to {intent.label.lower()}. This is inside my tool surface.")
        add_tool("ok")
        answer = _pick(rng, intent.resolved, "Here is what I found.")
        spans.append(SpanDraft("assistant_message", None, answer))
        if rng.random() < 0.35:
            spans.append(SpanDraft("user_message", None, rng.choice(GRATITUDE)))
        what = f"The agent recognised the request, used {intent.tool or 'its own knowledge'}, and answered it directly."
        satisfied, sentiment, gave_up, ended_by = "yes", "positive", False, "user"

    elif kind == UNMET_CAPABILITY:
        add_thinking("I do not have a tool that covers this request.")
        if intent.tool and rng.random() < 0.5:
            add_tool("ok")
        deflection = _pick(rng, intent.deflect, "I am not able to do that.")
        spans.append(SpanDraft("assistant_message", None, deflection))
        followup = _pick(rng, intent.followups or FRUSTRATION, rng.choice(FRUSTRATION))
        spans.append(SpanDraft("user_message", None, followup))
        rephrases = 1
        spans.append(SpanDraft("assistant_message", None, _pick(rng, intent.deflect, "I am not able to do that.")))
        if rng.random() < 0.5:
            spans.append(SpanDraft("user_message", None, rng.choice(FRUSTRATION)))
            rephrases = 2
        what = f"The user asked for something outside the agent's capabilities: {intent.gap_reason or 'no tool covers this request'}."
        satisfied = "no"
        sentiment = rng.choice(("frustrated", "neutral", "frustrated"))
        gave_up = rephrases >= 2
        ended_by = "user" if gave_up else "assistant"

    elif kind == FAKE_SUCCESS:
        add_thinking("I will report this as done even though no tool performed it.")
        claim = _pick(rng, intent.hallucinated, "I have completed that for you.")
        spans.append(SpanDraft("assistant_message", None, claim))
        followup = _pick(rng, intent.followups, "I do not see that anywhere. Did it really happen?")
        spans.append(SpanDraft("user_message", None, followup))
        rephrases = 1
        spans.append(SpanDraft("assistant_message", None, "It has been submitted and should appear shortly."))
        what = (
            "The agent claimed to complete an action it has no tool for and then repeated the claim "
            "when the user could not find any evidence of it."
        )
        satisfied, sentiment, gave_up, ended_by = "no", "frustrated", True, "user"

    elif kind == TOOL_LOOP:
        add_thinking("Let me look that up.")
        repeats = rng.randint(3, 5)
        for _ in range(repeats):
            add_tool("ok")
        repeated = repeats
        spans.append(SpanDraft("assistant_message", None, _pick(rng, intent.deflect, "Let me check that again.")))
        spans.append(SpanDraft("user_message", None, rng.choice(FRUSTRATION)))
        rephrases = rng.randint(1, 3)
        add_tool("ok")
        repeated += 1
        spans.append(SpanDraft("assistant_message", None, "I am still retrieving that record."))
        what = (
            f"The agent called {intent.tool} {repeated} times with identical arguments and never converted "
            "the result into an answer."
        )
        satisfied, sentiment, gave_up, ended_by = "no", "frustrated", True, "user"

    elif kind == TOOL_ERROR:
        add_thinking("Calling the tool for this record.")
        add_tool("error")
        spans.append(
            SpanDraft(
                "error",
                intent.tool,
                intent.tool_err or '{"error":"tool_failed"}',
                status="error",
                error_code="tool_upstream_error",
            )
        )
        error_code = "tool_upstream_error"
        spans.append(SpanDraft("assistant_message", None, "That lookup failed on my side. Please try again shortly."))
        if rng.random() < 0.6:
            spans.append(SpanDraft("user_message", None, "It has failed three times already."))
            rephrases = 1
        what = f"The {intent.tool} call returned an upstream error and the agent had no fallback path."
        satisfied, sentiment, gave_up, ended_by = "no", "frustrated", rephrases > 0, "assistant"

    elif kind == WRONG_ANSWER:
        add_thinking("I will handle this with the closest tool I have.")
        add_tool("ok")
        wrong = _pick(rng, intent.wrong_answer_as, "I have taken a different action instead.")
        spans.append(SpanDraft("assistant_message", None, wrong))
        spans.append(SpanDraft("user_message", None, _pick(rng, intent.followups, "That is not what I asked for.")))
        rephrases = 1
        spans.append(SpanDraft("assistant_message", None, "That is the process I am able to follow."))
        what = "The agent answered a neighbouring question and never addressed what the user actually asked for."
        satisfied, sentiment, gave_up, ended_by = "no", "frustrated", True, "user"

    elif kind == HALLUCINATION:
        add_thinking("I will answer from memory.")
        claim = _pick(rng, intent.hallucinated, "That information is available and current.")
        spans.append(SpanDraft("assistant_message", None, claim))
        if rng.random() < 0.5:
            spans.append(SpanDraft("user_message", None, _pick(rng, intent.followups, "That turned out to be wrong.")))
            rephrases = 1
            spans.append(SpanDraft("assistant_message", None, "Apologies, please verify on the product page."))
        what = "The agent produced a specific, checkable claim without calling any tool that could support it."
        satisfied = "no" if rephrases else "unclear"
        sentiment = "frustrated" if rephrases else "neutral"
        gave_up = bool(rephrases)
        ended_by = "user" if rephrases else "assistant"

    elif kind == CONTRADICTION:
        add_thinking("Checking whether this is still possible.")
        add_tool("ok")
        text = _pick(rng, intent.contradiction, "That is not possible. I have completed it.")
        spans.append(SpanDraft("assistant_message", None, text))
        spans.append(SpanDraft("user_message", None, _pick(rng, intent.followups, "Which of those two is true?")))
        rephrases = 1
        spans.append(SpanDraft("assistant_message", None, "Please check your email for confirmation."))
        what = "The agent asserted the action was impossible and simultaneously claimed to have completed it."
        satisfied, sentiment, gave_up, ended_by = "unclear", "frustrated", True, "user"

    elif kind == REFUSAL:
        add_thinking("This looks like something I should decline.")
        text = _pick(rng, intent.refusal_text, "I am not able to help with that.")
        spans.append(SpanDraft("assistant_message", None, text))
        spans.append(SpanDraft("user_message", None, _pick(rng, intent.followups, "Why not? It is on your own website.")))
        rephrases = 1
        spans.append(SpanDraft("assistant_message", None, text))
        what = "The agent refused a request that its own published material already answers."
        satisfied, sentiment, gave_up, ended_by = "no", "frustrated", True, "user"

    elif kind == PROVIDER_ERROR:
        add_thinking("Preparing the response.")
        if intent.tool:
            add_tool("ok")
        error_code = rng.choice(SLOTS["provider_error_code"])
        spans.append(
            SpanDraft(
                "error",
                "provider_request",
                f"Provider returned {error_code} before the assistant could reply.",
                status="error",
                error_code=error_code,
            )
        )
        what = f"An upstream provider error ({error_code}) interrupted the response before the agent could answer."
        satisfied, sentiment, gave_up, ended_by = "no", "unclear", False, "system"

    elif kind == TIMEOUT:
        add_thinking("This will need a broad search.")
        add_tool("ok")
        error_code = "gateway_upstream_timeout_504"
        spans.append(
            SpanDraft(
                "error",
                "provider_request",
                "The request timed out before the search completed.",
                status="error",
                error_code=error_code,
            )
        )
        what = "The agent started an unbounded search with no checkpoints and hit the provider timeout."
        satisfied, sentiment, gave_up, ended_by = "no", "frustrated", False, "system"

    else:  # CONTEXT_OVERFLOW
        add_thinking("Loading the whole corpus into context.")
        if intent.tool:
            add_tool("ok")
        error_code = "upstream_context_length_exceeded"
        spans.append(
            SpanDraft(
                "error",
                "provider_request",
                "Context length exceeded while assembling the request.",
                status="error",
                error_code=error_code,
            )
        )
        spans.append(
            SpanDraft(
                "assistant_message",
                None,
                _pick(rng, intent.deflect, "I could not complete that in one pass."),
            )
        )
        what = "The agent attempted a single-pass read of more material than the context window allows."
        satisfied, sentiment, gave_up, ended_by = "no", "frustrated", False, "assistant"

    last_type = spans[-1].type
    summary = _summarise(intent, kind)
    return Episode(
        app=app.name,
        intent_key=intent.key,
        intent_label=intent.label,
        kind=kind,
        outcome=outcome,
        failure_mode=failure_mode,
        spans=spans,
        user_request=ask,
        what_happened=what,
        summary=summary,
        tools_called=tools_called,
        repeated_identical_calls=repeated,
        rephrase_count=rephrases,
        user_gave_up=gave_up,
        intent_satisfied=satisfied,
        sentiment_end=sentiment,
        ended_by=ended_by if last_type != "error" else "system",
        model=model,
        provider=provider,
        error_code=error_code,
    )


_SUMMARY = {
    RESOLVED: "{label}: handled and answered.",
    UNMET_CAPABILITY: "{label}: outside what the agent can do, so it deflected.",
    FAKE_SUCCESS: "{label}: the agent claimed success with no tool behind it.",
    TOOL_LOOP: "{label}: the same tool call repeated without producing an answer.",
    TOOL_ERROR: "{label}: the tool call failed upstream.",
    WRONG_ANSWER: "{label}: the agent acted on a different question.",
    HALLUCINATION: "{label}: the agent stated an unverified specific.",
    CONTRADICTION: "{label}: the agent contradicted itself while acting.",
    REFUSAL: "{label}: the agent refused an answerable request.",
    PROVIDER_ERROR: "{label}: interrupted by an upstream provider error.",
    TIMEOUT: "{label}: timed out before producing evidence.",
    CONTEXT_OVERFLOW: "{label}: exceeded the context window mid-request.",
}


def _summarise(intent: Intent, kind: str) -> str:
    return _SUMMARY[kind].format(label=intent.label)


def app_weights() -> list[float]:
    return [app.weight for app in APPS]


def intent_index() -> dict[str, Intent]:
    return {f"{app.name}/{intent.key}": intent for app in APPS for intent in app.intents}


def all_tools() -> list[str]:
    seen: list[str] = []
    for app in APPS:
        for tool in app.tools:
            if tool not in seen:
                seen.append(tool)
    return seen


def iter_intents() -> Iterable[tuple[App, Intent]]:
    for app in APPS:
        for intent in app.intents:
            yield app, intent
