"""
Adapters — 외부 시스템과 하네스를 연결하는 어댑터 계층

각 어댑터는 외부 시스템의 언어를 하네스의 언어로 번역한다.
외부 시스템은 하네스 내부를 몰라도 되고, 하네스도 외부 시스템을 몰라도 된다.

현재 어댑터:
- XgenAdapter: xgen-workflow ↔ 하네스 파이프라인
"""

from .xgen import XgenAdapter

__all__ = ["XgenAdapter"]
