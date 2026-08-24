# Raft prototype content

Fake but plausible content for paper.design mockups. App: support bot for an online clothing store.
All numbers are internally consistent. Do not change one without changing the others.

---

## 1. Trace list rows (8)

| # | Summary | Outcome | Cost | Duration |
|---|---------|---------|------|----------|
| 1 | Asked whether the jacket runs small; agent linked the size chart three times and never answered | unsatisfied | $0.0041 | 12.3s |
| 2 | Address change after dispatch; agent called `lookup_order` four times, never said it was too late | unsatisfied | $0.0112 | 31.8s |
| 3 | Wanted to swap a size 10 for a 12; agent processed a refund instead | wrong action | $0.0089 | 24.1s |
| 4 | Does the linen shirt shrink in the wash? Agent refused, said it cannot give care advice | refusal | $0.0018 | 4.2s |
| 5 | Missing item from a two-item order; escalated to a human in two turns | resolved | $0.0022 | 6.7s |
| 6 | Asked for a discount code; agent offered SPRING20, which does not exist | hallucination | $0.0031 | 8.9s |
| 7 | Boots for wide feet; agent asked for an order number instead of answering | unsatisfied | $0.0027 | 9.4s |
| 8 | Tried to cancel 20 minutes after ordering; agent said cancellation was impossible, then cancelled it | contradiction | $0.0064 | 18.2s |

**Layout stress note:** summaries range from 10 to 17 words on purpose. Row height must survive the longest one without truncating.

---

## 2. Answer screen

**Question typed:** what do my users struggle with most?

**Path line:** Clustered 312 unsatisfied conversations out of 847. No new aspect needed.

### Headline

| Rank | Cluster | Conversations | Share of unsatisfied |
|------|---------|---------------|---------------------|
| 1 | Fit and sizing questions the agent cannot answer | 89 | 29% |
| 2 | Changes to an order after it has shipped | 61 | 20% |
| 3 | Returns falling outside the 30-day window | 44 | 14% |
| 4 | Fabric and care questions | 38 | 12% |
| | 23 smaller clusters | 80 | 26% |

### Interpretation (one sentence)

Two of your top four are questions your own product pages already answer, and the agent has no product data tool, so it deflects instead.

### Verbatim quotes

**Cluster 1, fit and sizing**
> i'm a 12 in everything else is this one true to size or not, the chart doesn't tell me anything

**Cluster 2, changes after shipping**
> it's been 20 minutes how is it already too late to change the address

**Cluster 3, returns outside the window**
> the coat was a gift i didn't even open it until christmas thats not 30 days thats not my fault

### Actions on the answer

- Show the work (opens the executed code)
- Show me the traces behind this (drills into a filtered trace list)

---

## 3. Autopsy

For trace 3.

> The user wanted to exchange a size 10 jacket for a size 12 and said so in her first message. The agent called `lookup_order`, found the order, then called `process_refund` without ever checking stock for the size 12. It confirmed the refund as though that were the request; she replied twice that she wanted the larger size, and the agent repeated the refund confirmation both times. The refund completed, the exchange never happened, and the store lost the sale.
>
> 14 turns, 3 tool calls, $0.0089, 24.1 seconds.

---

## 4. Timeline spans for trace 2

Use this to test width-as-cost and the collapsed repeat block.

| Span | Type | Cost | Note |
|------|------|------|------|
| 0 | user_message | $0 | narrow |
| 1 | thinking | $0.0008 | narrow |
| 2-5 | tool_call `lookup_order` | $0.0072 | **collapsed, marked x4**, widest block on the track |
| 6 | thinking | $0.0011 | medium |
| 7 | assistant_message | $0.0021 | medium |

---

## 5. Home page pre-made insights

Reuse the answer card component for each.

1. **Top struggle this week:** fit and sizing questions, 89 conversations, up 12% week over week
2. **Top unmet request:** exchange an item for a different size, 61 conversations, no tool exists
3. **Spend by outcome:** $4.12 unsatisfied, $2.87 resolved, $0.94 refusal
