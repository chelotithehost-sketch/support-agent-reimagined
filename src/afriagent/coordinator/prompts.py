"""Coordinator system prompt + few-shot examples for the dispatch LLM."""

from __future__ import annotations

from typing import Any

# ── System Prompt Template ────────────────────────────────────────
# Placeholders: {tool_registry}, {self_model_state}, {provider_health}

COORDINATOR_SYSTEM_PROMPT = """You are the CoordinatorBrain — the central dispatcher for an AI customer support agent serving African web hosting customers (Kenya-focused).

Your job: analyze an incoming customer message and produce a DISPATCH PLAN that tells the agent exactly what to do.

## Available Tools
{tool_registry}

## Self-Model State (learned reliability data)
{self_model_state}

## LLM Provider Health
{provider_health}

## Output Schema
You MUST respond ONLY in valid JSON matching this schema:
{{
  "intent": "billing|outage|general|hostile|unclear",
  "urgency": 1-5,
  "language": "en|sw|sheng|other",
  "steps": [
    {{
      "tool": "tool_name or null",
      "llm_provider": "provider_name or null",
      "params": {{}}
    }}
  ],
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation of your decision"
}}

## Rules
1. Use tool calls when the request maps to a concrete action (check invoice, push payment, DNS lookup, etc.)
2. Use LLM-only steps for conversational responses (greetings, general info, clarification)
3. If the message is hostile or threatening, set intent=hostile and add an escalation step
4. For M-Pesa related queries, always include a check_invoice step before mpesa_push
5. For DNS/hosting issues, always include check_domain_dns as first step
6. Prefer the healthiest LLM provider (lowest error_streak, lowest latency)
7. If a tool's reliability is below 0.7, add a fallback LLM step after it
8. Respond in the customer's detected language
9. Max 4 steps per plan
10. If confidence < 0.5, set intent=unclear and add a clarification LLM step
"""

# ── Few-Shot Examples ─────────────────────────────────────────────

FEW_SHOT_EXAMPLES: list[dict[str, Any]] = [
    # 1. Billing — M-Pesa payment inquiry
    {
        "input": "Nataka kulipa invoice yangu ya hosting, niangalie M-Pesa",
        "context": {"detected_language": "sw", "customer_has_invoices": True},
        "output": {
            "intent": "billing",
            "urgency": 3,
            "language": "sw",
            "steps": [
                {"tool": "check_invoice", "llm_provider": None, "params": {"status": "Unpaid"}},
                {"tool": "mpesa_push", "llm_provider": None, "params": {}},
            ],
            "confidence": 0.92,
            "reasoning": "Customer wants to pay hosting invoice via M-Pesa in Swahili. Check unpaid invoices first, then initiate STK push.",
        },
    },
    # 2. Outage — site down report
    {
        "input": "My website example.co.ke is completely down since morning! This is affecting my business!",
        "context": {"detected_language": "en", "customer_has_services": True},
        "output": {
            "intent": "outage",
            "urgency": 5,
            "language": "en",
            "steps": [
                {"tool": "check_domain_dns", "llm_provider": None, "params": {"domain": "example.co.ke"}},
                {"tool": "lookup_customer", "llm_provider": None, "params": {}},
                {"tool": None, "llm_provider": "openai", "params": {"task": "explain_dns_results_and_remediate"}},
            ],
            "confidence": 0.95,
            "reasoning": "Critical outage report. DNS check first to diagnose, then customer lookup for context, then generate resolution steps.",
        },
    },
    # 3. General — greeting in Sheng
    {
        "input": "Sasa buda, niaje?",
        "context": {"detected_language": "sheng"},
        "output": {
            "intent": "general",
            "urgency": 1,
            "language": "sheng",
            "steps": [
                {"tool": None, "llm_provider": "ollama", "params": {"task": "greet_in_sheng"}},
            ],
            "confidence": 0.88,
            "reasoning": "Casual Sheng greeting. Low urgency, respond warmly in Sheng to build rapport.",
        },
    },
    # 4. Billing — unpaid invoice with urgency
    {
        "input": "I have an overdue invoice and my account is about to be suspended. Can I pay now?",
        "context": {"detected_language": "en", "customer_has_invoices": True, "account_at_risk": True},
        "output": {
            "intent": "billing",
            "urgency": 4,
            "language": "en",
            "steps": [
                {"tool": "check_invoice", "llm_provider": None, "params": {"status": "Overdue"}},
                {"tool": "mpesa_push", "llm_provider": None, "params": {}},
                {"tool": None, "llm_provider": "openai", "params": {"task": "confirm_payment_and_warn_about_suspension"}},
            ],
            "confidence": 0.94,
            "reasoning": "Overdue invoice with suspension risk. High urgency. Get invoice, push payment, then confirm and explain next steps.",
        },
    },
    # 5. Hostile — abusive customer
    {
        "input": "You people are scammers! I've been waiting 3 days and nobody fixed my email! I want a refund NOW!",
        "context": {"detected_language": "en", "sentiment": "frustrated"},
        "output": {
            "intent": "hostile",
            "urgency": 5,
            "language": "en",
            "steps": [
                {"tool": "create_support_ticket", "llm_provider": None, "params": {"priority": "High", "subject": "Escalation: Email issue + refund request"}},
                {"tool": None, "llm_provider": "anthropic", "params": {"task": "de_escalate_and_acknowledge"}},
            ],
            "confidence": 0.90,
            "reasoning": "Hostile customer with unresolved email issue and refund demand. Create high-priority ticket immediately, then de-escalate with empathetic response.",
        },
    },
    # 6. DNS propagation check
    {
        "input": "I changed my nameservers yesterday but my site still shows the old page. Is DNS propagation done?",
        "context": {"detected_language": "en"},
        "output": {
            "intent": "outage",
            "urgency": 3,
            "language": "en",
            "steps": [
                {"tool": "check_domain_dns", "llm_provider": None, "params": {}},
                {"tool": None, "llm_provider": "openai", "params": {"task": "explain_propagation_status"}},
            ],
            "confidence": 0.91,
            "reasoning": "DNS propagation inquiry. Check current DNS state, then explain propagation timeline and next steps.",
        },
    },
    # 7. Unclear — vague message
    {
        "input": "help me",
        "context": {"detected_language": "en"},
        "output": {
            "intent": "unclear",
            "urgency": 2,
            "language": "en",
            "steps": [
                {"tool": None, "llm_provider": "ollama", "params": {"task": "ask_clarification"}},
            ],
            "confidence": 0.40,
            "reasoning": "Message too vague to determine intent. Need to ask what specific help is needed.",
        },
    },
    # 8. Swahili — technical support
    {
        "input": "Barua pepe yangu haifanyi kazi. Ninatumia Outlook.",
        "context": {"detected_language": "sw"},
        "output": {
            "intent": "outage",
            "urgency": 3,
            "language": "sw",
            "steps": [
                {"tool": "lookup_customer", "llm_provider": None, "params": {}},
                {"tool": None, "llm_provider": "ollama", "params": {"task": "email_troubleshooting_swahili"}},
            ],
            "confidence": 0.85,
            "reasoning": "Email not working, customer uses Outlook, speaking Swahili. Look up customer details first, then provide Outlook email troubleshooting in Swahili.",
        },
    },
    # 9. Billing — new hosting purchase inquiry
    {
        "input": "How much is your business hosting plan? I want to host my e-commerce site.",
        "context": {"detected_language": "en"},
        "output": {
            "intent": "billing",
            "urgency": 2,
            "language": "en",
            "steps": [
                {"tool": None, "llm_provider": "openai", "params": {"task": "sales_pitch_hosting_plans"}},
            ],
            "confidence": 0.87,
            "reasoning": "Sales/pricing inquiry about business hosting. Respond with plan details and pricing. No tool calls needed.",
        },
    },
    # 10. Mixed Sheng + English — payment issue
    {
        "input": "Eh maze, my M-Pesa payment went through but the invoice is still showing unpaid in my account. Fix this buda!",
        "context": {"detected_language": "sheng", "sentiment": "frustrated"},
        "output": {
            "intent": "billing",
            "urgency": 4,
            "language": "sheng",
            "steps": [
                {"tool": "check_invoice", "llm_provider": None, "params": {}},
                {"tool": "lookup_customer", "llm_provider": None, "params": {}},
                {"tool": None, "llm_provider": "openai", "params": {"task": "investigate_payment_mismatch"}},
            ],
            "confidence": 0.88,
            "reasoning": "Sheng-speaking customer, frustrated about M-Pesa payment not reflecting. Check invoice status, look up customer for M-Pesa transaction history, then investigate the mismatch.",
        },
    },
]


def build_system_prompt(
    tool_registry: dict[str, Any],
    self_model_state: dict[str, Any],
    provider_health: dict[str, Any],
) -> str:
    """Build the full coordinator system prompt with injected context."""
    import json

    tool_lines = []
    for name, info in tool_registry.items():
        tool_lines.append(
            f"- {name}: {info.get('description', 'N/A')} "
            f"(latency: {info.get('latency_profile', 'unknown')}, "
            f"reliability: {self_model_state.get('tool_reliability', {}).get(name, 'unknown')})"
        )
    tool_text = "\n".join(tool_lines) if tool_lines else "No tools registered."

    provider_lines = []
    for name, health in provider_health.items():
        status = health.get("status", "unknown")
        latency = health.get("avg_latency_ms", "?")
        streak = health.get("error_streak", 0)
        provider_lines.append(f"- {name}: {status} (latency: {latency}ms, error_streak: {streak})")
    provider_text = "\n".join(provider_lines) if provider_lines else "No providers available."

    return COORDINATOR_SYSTEM_PROMPT.format(
        tool_registry=tool_text,
        self_model_state=json.dumps(self_model_state, indent=2, default=str),
        provider_health=provider_text,
    )


def get_few_shot_messages() -> list[dict[str, str]]:
    """Convert few-shot examples into chat messages for the coordinator LLM."""
    import json

    messages: list[dict[str, str]] = []
    for ex in FEW_SHOT_EXAMPLES:
        user_msg = (
            f"Customer message: {ex['input']}\n"
            f"Context: {json.dumps(ex['context'])}"
        )
        assistant_msg = json.dumps(ex["output"], indent=2)
        messages.append({"role": "user", "content": user_msg})
        messages.append({"role": "assistant", "content": assistant_msg})
    return messages
