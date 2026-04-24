"""
Adapters — 외부 시스템과 하네스를 연결하는 어댑터 계층

각 어댑터는 외부 시스템의 언어를 하네스의 언어로 번역한다.
외부 시스템은 하네스 내부를 몰라도 되고, 하네스도 외부 시스템을 몰라도 된다.

v0.22.0 — XgenAdapter 는 이식측(xgen-workflow/harness_bridge)으로 이관됨.
엔진 코어는 ResourceRegistry 프로토콜·Protocol 기반 duck-typing 만 노출.
"""

from .resource_registry import ResourceRegistry, ResourceInfo

__all__ = ["ResourceRegistry", "ResourceInfo"]
