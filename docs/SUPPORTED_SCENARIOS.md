# Supported Support Scenarios

The system handles these scenarios end-to-end today. Each has a dedicated playbook in `knowledge/hosting_playbooks.yaml` with diagnostic steps, common causes, and resolution templates.

## Email Issues

| Scenario | Intent | Resolution Path | Typical Turns |
|----------|--------|-----------------|---------------|
| Email not sending | TECHNICAL | MX/SPF/DKIM check → DNS fix → propagation wait | 2-3 |
| Email not receiving | TECHNICAL | MX verification → email routing → spam folder check | 2-3 |
| Email bounceback | TECHNICAL | Check bounce code → verify recipient → check blacklist | 2-4 |
| Outlook/Thunderbird config | TECHNICAL | IMAP/SMTP settings → port verification → SSL check | 2-3 |
| Email quota exceeded | BILLING | Check disk usage → delete old emails → upgrade plan | 2-3 |

## DNS & Domain

| Scenario | Intent | Resolution Path | Typical Turns |
|----------|--------|-----------------|---------------|
| DNS not resolving | TECHNICAL | Nameserver check → A record verification → propagation wait | 2-3 |
| SSL certificate errors | TECHNICAL | Cert status check → renewal → DNS verification | 2-4 |
| Domain transfer | TECHNICAL | EPP code → unlock domain → registrar coordination | 3-5 |
| Subdomain not working | TECHNICAL | DNS record check → server config → .htaccess | 2-3 |
| DNS propagation delay | TECHNICAL | Explain timeline → verify nameservers → suggest checker tools | 1-2 |

## Website & Hosting

| Scenario | Intent | Resolution Path | Typical Turns |
|----------|--------|-----------------|---------------|
| Website down (500/503) | TECHNICAL | Error log check → resource usage → .htaccess → PHP version | 2-4 |
| WordPress white screen | TECHNICAL | Debug mode → plugin conflict → memory limit → theme check | 3-5 |
| cPanel login failed | TECHNICAL | Password reset → IP unblock → browser cache clear | 2-3 |
| Slow website | TECHNICAL | Resource check → cache config → database optimization | 2-4 |
| File upload failed | TECHNICAL | Check file size limits → permissions → disk space | 2-3 |

## Billing & Payments

| Scenario | Intent | Resolution Path | Typical Turns |
|----------|--------|-----------------|---------------|
| Invoice payment (M-Pesa) | BILLING | Check invoice → STK Push → confirm payment | 1-2 |
| Payment not reflecting | BILLING | Query M-Pesa status → verify receipt → manual reconciliation | 2-3 |
| Double charge | BILLING | Verify transactions → process refund → confirm | 2-3 |
| Account suspension | BILLING | Check overdue invoices → payment → unsuspend | 2-3 |
| Plan upgrade/downgrade | SALES | Show plans → compare features → process change | 2-3 |

## Multi-Turn Troubleshooting

The system handles incomplete user input and confused users through the coordinator's replanning logic:

**Example: Vague initial message**
```
Customer: "help me"
→ Coordinator: intent=unclear, confidence=0.4 → clarification step
→ Brain: "I'd be happy to help! Could you tell me more about what
   you're experiencing? For example, is it an email issue, website
   problem, or billing question?"
Customer: "my email is broken"
→ Coordinator: intent=outage, confidence=0.85 → DNS check + troubleshooting
→ Brain: provides specific email troubleshooting steps
```

**Example: Confused user, multiple issues**
```
Customer: "my site is slow and also I can't pay my invoice"
→ Coordinator: detects mixed intents → prioritizes billing (higher urgency)
→ Brain: addresses billing first, then offers to help with performance
→ "Let me help you with the invoice first. I'll check your outstanding
   balance. After that, we can look at the website performance issue."
```

**Example: Escalation after failed resolution**
```
Customer: "I already tried that, it didn't work"
→ Coordinator: replan cycle 1 → try different approach
→ Brain: "Let me try a different approach. Can you tell me what error
   message you're seeing?"
Customer: "still not working, this is ridiculous"
→ Coordinator: replan cycle 2 → frustration detected
→ Brain: "I understand this is frustrating. Let me escalate this to
   our technical team who can investigate more deeply."
→ create_support_ticket(priority="High") + escalation message
```
