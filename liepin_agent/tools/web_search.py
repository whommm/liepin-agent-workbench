"""Web search tool using Tavily API to gather sourcing intelligence."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from ..core.config import ConfigManager

logger = logging.getLogger(__name__)


@dataclass
class WebSearchIntel:
    """Structured intelligence from web search."""

    raw_results: List[Dict[str, str]] = field(default_factory=list)
    summary: str = ""
    suggested_keywords: List[str] = field(default_factory=list)
    target_companies: List[str] = field(default_factory=list)
    transferable_directions: List[str] = field(default_factory=list)
    industry_insights: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "raw_results": self.raw_results,
            "summary": self.summary,
            "suggested_keywords": self.suggested_keywords,
            "target_companies": self.target_companies,
            "transferable_directions": self.transferable_directions,
            "industry_insights": self.industry_insights,
        }


class WebSearchTool:
    """Search the web for industry intelligence to improve sourcing strategy."""

    TAVILY_API_URL = "https://api.tavily.com/search"

    def __init__(
        self,
        api_key: Optional[str] = None,
        config_manager: Optional[ConfigManager] = None,
    ):
        manager = config_manager or ConfigManager()
        self.api_key = api_key or manager.config.tavily_api_key or ""
        self.enabled = bool(self.api_key)
        self.timeout = 30

    def search(
        self,
        query: str,
        max_results: int = 3,
        search_depth: str = "basic",
    ) -> List[Dict[str, str]]:
        """Execute a single Tavily search query."""
        if not self.enabled:
            logger.debug("WebSearchTool: no API key configured")
            return []

        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": True,
        }

        try:
            response = httpx.post(
                self.TAVILY_API_URL,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            results: List[Dict[str, str]] = []
            for item in data.get("results", []):
                content = item.get("content", "")
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": content[:1200] if content else "",
                })
            answer = data.get("answer")
            if answer:
                results.insert(0, {
                    "title": "联网综合情报",
                    "url": "",
                    "content": str(answer)[:1200],
                })
            logger.info(
                "WebSearchTool: query='%s' results=%s",
                query,
                len(results),
            )
            return results
        except Exception as exc:
            logger.error("WebSearchTool search failed: %s", exc)
            return []

    def gather_intelligence(
        self,
        jd_text: str,
        current_query: str,
        used_queries: List[str],
        noise_patterns: List[str],
        custom_queries: Optional[List[str]] = None,
    ) -> WebSearchIntel:
        """Gather web intelligence based on current sourcing困境."""
        if not self.enabled:
            return WebSearchIntel(summary="Web search not configured.")

        queries = custom_queries if custom_queries else self._build_search_queries(jd_text, current_query, noise_patterns)
        all_results: List[Dict[str, str]] = []
        for q in queries:
            results = self.search(q, max_results=3)
            all_results.extend(results)

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_results: List[Dict[str, str]] = []
        for r in all_results:
            url = r.get("url", "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            unique_results.append(r)

        # Build a text summary for the LLM
        summary_parts: List[str] = []
        for r in unique_results[:10]:
            title = r.get("title", "")
            content = r.get("content", "")
            if title or content:
                summary_parts.append(f"【{title}】{content}")

        return WebSearchIntel(
            raw_results=unique_results,
            summary="\n\n".join(summary_parts),
        )

    @staticmethod
    def _build_search_queries(
        jd_text: str,
        current_query: str,
        noise_patterns: List[str],
    ) -> List[str]:
        """Build search queries based on noise patterns."""
        queries: List[str] = []
        noise_text = " ".join(noise_patterns or [])
        jd_snippet = (jd_text or "")[:80]

        # Strategy based on noise type
        if any(k in noise_text for k in ("太窄", "结果极少", "为空", "太少", "empty")):
            queries.append(f"{current_query} 行业 同义词 相关岗位 招聘要求")

        if any(k in noise_text for k in ("太宽", "量大", "匹配度低", "噪音", "wide")):
            queries.append(f"{jd_snippet} 细分方向 专业技能 核心能力要求")

        if any(k in noise_text for k in ("行业", "误匹配", "错配", "跨行业", "mapping")):
            queries.append(f"{current_query} 可迁移行业 人才分布 相关领域")

        if any(k in noise_text for k in ("公司", "对标", "竞品", "目标公司", "target")):
            queries.append(f"{jd_snippet} 目标公司 行业领先企业 人才画像")

        # Fallback: general intelligence gathering
        if not queries:
            queries.append(f"{jd_snippet} 岗位要求 核心技能 行业背景")
            queries.append(f"{current_query} 招聘 人才市场 技能要求")

        return queries[:3]
