//! PyO3 바인딩 — Python에서 직접 import 가능한 네이티브 모듈
//!
//! ```python
//! import xgen_harness
//!
//! # 동기 호출 (내부에서 tokio 런타임 생성)
//! result = xgen_harness.run(
//!     text="분석해줘",
//!     provider="anthropic",
//!     model="claude-sonnet-4-6",
//!     api_key="sk-...",
//!     stages=["input", "system_prompt", "plan", "llm", "execute", "complete"],
//! )
//!
//! # 스트리밍 — 이벤트 리스트 반환
//! events, result = xgen_harness.run_with_events(
//!     text="분석해줘",
//!     provider="anthropic",
//!     model="claude-sonnet-4-6",
//!     api_key="sk-...",
//! )
//! for e in events:
//!     print(e["event"], e["data_json"])
//! ```

use pyo3::prelude::*;
use pyo3::types::PyDict;
use tokio::runtime::Runtime;

use crate::builder::HarnessBuilder;
use crate::events::SseEvent;

fn build_harness(
    text: &str,
    provider: &str,
    model: &str,
    api_key: Option<&str>,
    system_prompt: Option<&str>,
    stages: Option<Vec<String>>,
    tools: Option<Vec<String>>,
    temperature: f64,
    max_tokens: u32,
    max_retries: u32,
    eval_threshold: f64,
) -> HarnessBuilder {
    let mut builder = HarnessBuilder::new()
        .text(text)
        .provider(provider, model)
        .temperature(temperature)
        .max_tokens(max_tokens)
        .max_retries(max_retries)
        .eval_threshold(eval_threshold);

    if let Some(key) = api_key {
        builder = builder.api_key(key);
    }
    if let Some(prompt) = system_prompt {
        builder = builder.system_prompt(prompt);
    }
    if let Some(s) = stages {
        builder = builder.stages(s);
    }
    if let Some(t) = tools {
        builder = builder.tools(t);
    }

    builder
}

/// 동기 실행 — 결과 텍스트를 문자열로 반환
#[pyfunction]
#[pyo3(signature = (
    text,
    provider = "anthropic",
    model = "claude-sonnet-4-6",
    api_key = None,
    system_prompt = None,
    stages = None,
    tools = None,
    temperature = 0.7,
    max_tokens = 8192,
    max_retries = 3,
    eval_threshold = 0.7,
))]
fn run(
    text: &str,
    provider: &str,
    model: &str,
    api_key: Option<&str>,
    system_prompt: Option<&str>,
    stages: Option<Vec<String>>,
    tools: Option<Vec<String>>,
    temperature: f64,
    max_tokens: u32,
    max_retries: u32,
    eval_threshold: f64,
) -> PyResult<String> {
    let builder = build_harness(
        text, provider, model, api_key, system_prompt,
        stages, tools, temperature, max_tokens, max_retries, eval_threshold,
    );

    let rt = Runtime::new()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    rt.block_on(builder.run())
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

/// 이벤트 수집 실행 — (events_list, result_text) 튜플 반환
/// events_list: [{"event": "stage_enter", "data_json": "..."}, ...]
#[pyfunction]
#[pyo3(signature = (
    text,
    provider = "anthropic",
    model = "claude-sonnet-4-6",
    api_key = None,
    system_prompt = None,
    stages = None,
    tools = None,
    temperature = 0.7,
    max_tokens = 8192,
    max_retries = 3,
    eval_threshold = 0.7,
))]
fn run_with_events(
    py: Python<'_>,
    text: &str,
    provider: &str,
    model: &str,
    api_key: Option<&str>,
    system_prompt: Option<&str>,
    stages: Option<Vec<String>>,
    tools: Option<Vec<String>>,
    temperature: f64,
    max_tokens: u32,
    max_retries: u32,
    eval_threshold: f64,
) -> PyResult<(Vec<PyObject>, String)> {
    let builder = build_harness(
        text, provider, model, api_key, system_prompt,
        stages, tools, temperature, max_tokens, max_retries, eval_threshold,
    );

    let events = std::sync::Arc::new(std::sync::Mutex::new(Vec::<SseEvent>::new()));
    let events_clone = events.clone();

    let rt = Runtime::new()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    let result = rt
        .block_on(builder.run_with_events(move |event| {
            events_clone.lock().unwrap().push(event);
        }))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    // SseEvent → Python dict
    let collected = events.lock().unwrap();
    let py_events: Vec<PyObject> = collected
        .iter()
        .map(|event| {
            let dict = PyDict::new(py);
            let _ = dict.set_item("event", &event.event);
            let data_str = serde_json::to_string(&event.data).unwrap_or_default();
            let _ = dict.set_item("data_json", &data_str);
            if let Some(ref id) = event.id {
                let _ = dict.set_item("id", id);
            }
            dict.into_any().unbind()
        })
        .collect();

    Ok((py_events, result))
}

/// Python 모듈 등록
#[pymodule]
fn xgen_harness(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(run, m)?)?;
    m.add_function(wrap_pyfunction!(run_with_events, m)?)?;
    Ok(())
}
