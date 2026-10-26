---
# This is synthetic test content, not a personal draft.
title: "비공개 Markdown 예제"
excerpt: '원문 보존을 확인하는 합성 문서'
categories: [AI, LLM]
tags: [fixture, 한국어]
published: false
toc: true
mathjax: true
custom:
  nested:
    labels: [one, two]
    enabled: true
  quoted: "colon: value"
---

# 합성 문서

이 문서는 편집기 검증을 위해 만든 내용입니다. **강조**와 `inline_code`를 보존합니다.

$$x^2 + y^2 = z^2$$

```python
print("<script>literal code, not an executable element</script>")
```

| 항목 | 값 |
| --- | --- |
| 예제 | 42 |

![테스트 픽셀](../../../assets/images/fixture.png)

<div class="notice">원문 HTML도 그대로 보관합니다.</div>

{% include fixture_notice.html %}
