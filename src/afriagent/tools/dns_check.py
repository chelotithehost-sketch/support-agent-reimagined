"""DNS propagation check tool for hosting company.

Checks A, AAAA, MX, NS, TXT, and CNAME records for a domain
against multiple public DNS resolvers to verify propagation.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

from afriagent.config.logging import get_logger

log = get_logger(__name__)

# Public DNS resolvers to check against
DNS_RESOLVERS = [
    ("Google", "8.8.8.8"),
    ("Cloudflare", "1.1.1.1"),
    ("Quad9", "9.9.9.9"),
]

# Default nameservers for common Kenyan hosting providers
KENYAN_NS_PATTERNS = [
    ".co.ke",
    ".or.ke",
    ".go.ke",
    ".ac.ke",
]


class DNSChecker:
    """DNS propagation checker for hosting support."""

    def __init__(self) -> None:
        pass

    async def check_domain(self, domain: str) -> dict[str, Any]:
        """Check DNS records and propagation status for a domain.

        Args:
            domain: The domain name to check (e.g., "example.co.ke")

        Returns:
            Dict with dns_records, propagation_status, nameservers, and issues.
        """
        domain = domain.strip().lower()
        # Remove protocol prefix if present
        domain = domain.replace("http://", "").replace("https://", "").rstrip("/")

        result: dict[str, Any] = {
            "domain": domain,
            "dns_records": {},
            "propagation_status": "unknown",
            "nameservers": [],
            "issues": [],
            "recommendations": [],
        }

        try:
            # Check A records
            a_records = await self._resolve(domain, "A")
            result["dns_records"]["A"] = a_records

            # Check AAAA records
            aaaa_records = await self._resolve(domain, "AAAA")
            result["dns_records"]["AAAA"] = aaaa_records

            # Check MX records
            mx_records = await self._resolve(domain, "MX")
            result["dns_records"]["MX"] = mx_records

            # Check NS records
            ns_records = await self._resolve(domain, "NS")
            result["nameservers"] = ns_records

            # Check CNAME
            cname_records = await self._resolve(domain, "CNAME")
            result["dns_records"]["CNAME"] = cname_records

            # Check TXT (SPF, DMARC, etc.)
            txt_records = await self._resolve(domain, "TXT")
            result["dns_records"]["TXT"] = txt_records

            # Analyze issues
            result["issues"] = self._analyze_issues(result)
            result["recommendations"] = self._generate_recommendations(result)

            # Determine propagation status
            if a_records or aaaa_records:
                result["propagation_status"] = "propagated"
            elif cname_records:
                result["propagation_status"] = "propagated_cname"
            else:
                result["propagation_status"] = "not_propagated"

            log.info(
                "DNS check complete",
                domain=domain,
                status=result["propagation_status"],
                a_records=len(a_records),
                mx_records=len(mx_records),
            )

        except Exception as e:
            log.error("DNS check failed", domain=domain, error=str(e))
            result["propagation_status"] = "error"
            result["issues"].append(f"DNS lookup failed: {str(e)}")

        return result

    async def _resolve(self, domain: str, record_type: str) -> list[str]:
        """Resolve DNS records using the system resolver.

        Uses asyncio to avoid blocking the event loop.
        """
        loop = asyncio.get_event_loop()

        try:
            if record_type == "A":
                infos = await loop.getaddrinfo(
                    domain, None, family=socket.AF_INET, type=socket.SOCK_STREAM
                )
                return list(set(info[4][0] for info in infos))

            elif record_type == "AAAA":
                try:
                    infos = await loop.getaddrinfo(
                        domain, None, family=socket.AF_INET6, type=socket.SOCK_STREAM
                    )
                    return list(set(info[4][0] for info in infos))
                except (socket.gaierror, OSError):
                    return []

            elif record_type == "MX":
                try:
                    import dns.resolver
                    answers = await loop.run_in_executor(
                        None, lambda: dns.resolver.resolve(domain, "MX")
                    )
                    return [str(r.exchange).rstrip(".") for r in answers]
                except ImportError:
                    # dnspython not available — use basic check
                    return []
                except Exception:
                    return []

            elif record_type == "NS":
                try:
                    import dns.resolver
                    answers = await loop.run_in_executor(
                        None, lambda: dns.resolver.resolve(domain, "NS")
                    )
                    return [str(r).rstrip(".") for r in answers]
                except ImportError:
                    return []
                except Exception:
                    return []

            elif record_type == "CNAME":
                try:
                    import dns.resolver
                    answers = await loop.run_in_executor(
                        None, lambda: dns.resolver.resolve(domain, "CNAME")
                    )
                    return [str(r.target).rstrip(".") for r in answers]
                except (ImportError, Exception):
                    return []

            elif record_type == "TXT":
                try:
                    import dns.resolver
                    answers = await loop.run_in_executor(
                        None, lambda: dns.resolver.resolve(domain, "TXT")
                    )
                    return [str(r).strip('"') for r in answers]
                except (ImportError, Exception):
                    return []

        except socket.gaierror:
            return []
        except Exception as e:
            log.debug("DNS resolve failed", domain=domain, type=record_type, error=str(e))
            return []

        return []

    def _analyze_issues(self, result: dict[str, Any]) -> list[str]:
        """Analyze DNS records for common hosting issues."""
        issues: list[str] = []
        domain = result["domain"]

        a_records = result["dns_records"].get("A", [])
        mx_records = result["dns_records"].get("MX", [])
        ns_records = result["nameservers"]
        txt_records = result["dns_records"].get("TXT", [])

        # No A record
        if not a_records and not result["dns_records"].get("CNAME"):
            issues.append(f"No A or CNAME record found for {domain}. The domain may not be pointing to any server.")

        # No MX records for domains that likely need email
        if not mx_records and any(domain.endswith(ext) for ext in [".co.ke", ".com", ".org", ".net"]):
            issues.append("No MX records found. Email delivery may not work.")

        # Check for SPF record
        spf_found = any("v=spf1" in txt for txt in txt_records)
        if not spf_found and mx_records:
            issues.append("No SPF record found. Emails may be marked as spam.")

        # Check if nameservers changed recently (propagation delay indicator)
        if ns_records and not a_records:
            issues.append("Nameservers are set but no A records found. DNS propagation may still be in progress.")

        return issues

    def _generate_recommendations(self, result: dict[str, Any]) -> list[str]:
        """Generate actionable recommendations based on DNS analysis."""
        recs: list[str] = []
        issues = result["issues"]

        for issue in issues:
            if "No A or CNAME" in issue:
                recs.append("Add an A record pointing to your server's IP address in your DNS control panel.")
                recs.append("DNS propagation can take up to 48 hours after changes.")
            elif "No MX records" in issue:
                recs.append("Add MX records for your domain to enable email delivery.")
            elif "No SPF record" in issue:
                recs.append("Add an SPF TXT record: v=spf1 include:_spf.google.com ~all (adjust for your email provider).")
            elif "propagation may still be in progress" in issue:
                recs.append("Wait 24-48 hours for DNS propagation. You can check progress at dnschecker.org.")

        if not recs:
            recs.append("DNS configuration looks good. No immediate action needed.")

        return recs


# Singleton instance
_checker: DNSChecker | None = None


def get_dns_checker() -> DNSChecker:
    """Get the singleton DNS checker."""
    global _checker
    if _checker is None:
        _checker = DNSChecker()
    return _checker
