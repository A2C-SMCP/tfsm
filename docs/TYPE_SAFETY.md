# tfism 内部类型安全改进计划

## 文档概述

本文档分析 `tfism` 项目的内部类型安全现状，并提出系统性改进方案。**内部类型安全**是指项目自定义代码的类型安全，不包括状态机框架自动注入到用户模型的动态属性（如自动生成的触发方法）。

## 1. 当前状态（2025-12）

### 1.1 总体概况

- **类型检查状态**: ✅ 通过 `mypy --strict` 检查（0 错误）
- **类型注解覆盖率**: 100%（核心模块和扩展模块）
- **Type ignore 总数**: 243 个
- **Python 版本要求**: 3.11+
- **功能测试**: 3211 个测试全部通过

### 1.2 Type Ignore 统计分析

| 错误类型 | 数量 | 主要来源文件 | 根本原因 |
|---------|------|------------|---------|
| `union-attr` | 54 | nesting.py, diagrams_*.py | 联合类型属性访问，运行时才能确定具体类型 |
| `arg-type` | 41 | asyncio.py, nesting.py | 参数类型不兼容（如异步/同步混用） |
| `override` | 28 | asyncio.py, nesting.py | 子类方法签名与父类不兼容（LSP 违规） |
| `assignment` | 20 | nesting.py, asyncio.py | 赋值类型不匹配（元组解包等） |
| `attr-defined` | 18 | nesting.py, diagrams_*.py | 动态属性访问（state_cls.separator, events, states） |
| `func-returns-value` | 16 | asyncio.py | 异步函数调用未 await |
| `operator` | 8 | nesting.py | 运算符重载类型问题 |
| `no-any-return` | 6 | asyncio.py | 条件分支返回类型不一致 |
| `misc` | 6 | 多个文件 | 其他杂项类型问题 |
| `其他` | 26 | 多个文件 | 比较重叠、索引、列表项等 |

### 1.3 模块类型注解状态

| 模块 | Type Ignore | 主要问题 | 优先级 |
|------|------------|---------|-------|
| core.py | 0 | 无 | ✅ 完成 |
| asyncio.py | 81 | 异步/同步 LSP 违规 | 🔴 高 |
| nesting.py | 90 | 动态属性、联合类型 | 🔴 高 |
| diagrams.py | 4 | 联合类型 | 🟡 中 |
| diagrams_graphviz.py | 15 | 联合类型 | 🟡 中 |
| diagrams_mermaid.py | 5 | 联合类型 | 🟡 中 |
| diagrams_pygraphviz.py | 32 | 联合类型、动态属性 | 🟡 中 |
| factory.py | 9 | 类型推导 | 🟢 低 |
| markup.py | 3 | 类型推导 | 🟢 低 |
| states.py | 1 | 类型推导 | 🟢 低 |
| locking.py | 0 | 无（已处理） | ✅ 完成 |

## 2. 架构层类型问题分析

### 2.1 问题分类

#### A 类：架构限制（Architecture Limitations）

这类问题是设计上的权衡，在不破坏 API 兼容性的前提下无法完全解决。

1. **异步/同步方法 LSP 违规** (`override`)
   - **位置**: `asyncio.py`
   - **问题**: `AsyncMachine` 继承自 `Machine`，但将同步方法重写为异步方法
   - **影响方法**:
     - `dispatch()`, `add_model()`, `callbacks()`, `callback()`
     - `_can_trigger()`, `_process()`, `trigger_event()`
     - `AsyncState.enter()` / `exit()`
   - **当前方案**: `# type: ignore[override]` + TODO 注释
   - **改进难度**: 🔴 高（需要重构继承层次）

2. **子类方法签名扩展** (`override`, `arg-type`)
   - **位置**: `nesting.py`
   - **问题**: 子类扩展了父类方法接受的参数类型
     - `HierarchicalMachine.set_state()` 接受 `List[str]` 而非仅 `str | Enum | State`
     - `HierarchicalMachine._add_model_to_state()` 参数类型为 `NestedState`
   - **当前方案**: `# type: ignore[override]`
   - **改进难度**: 🟡 中（可使用 TypeVar 优化）

3. **动态属性访问** (`attr-defined`, `union-attr`)
   - **位置**: `nesting.py`, `diagrams_*.py`
   - **问题**: 状态机框架在运行时动态添加属性到类和实例
     - `state_cls.separator` - `NestedState.separator`
     - `state.events`, `state.states` - 嵌套状态的动态容器
     - 图形属性 - `model_graphs[id(model)]`
   - **当前方案**: `# type: ignore[attr-defined]`, `# type: ignore[union-attr]`
   - **改进难度**: 🟡 中（可使用 Protocol 显式声明）

#### B 类：类型系统限制（Type System Limitations）

这类问题源于 Python 类型系统的表达能力限制。

1. **联合类型属性访问** (`union-attr`)
   - **位置**: `nesting.py`, `diagrams_*.py`
   - **问题**: 对联合类型（如 `State | NestedState`）调用子类特有方法
   - **示例**:
     ```python
     state_tree = machine.build_state_tree(...)  # 返回 dict[str, Any] | list[Any]
     for state in states:  # states 可能是 dict 或 list
         # mypy 无法确定此时 states 的具体类型
     ```
   - **当前方案**: `# type: ignore[union-attr]`
   - **改进难度**: 🟡 中（使用 TypeGuard 或重构类型层次）

2. **元组解包类型推导** (`assignment`)
   - **位置**: `nesting.py`
   - **问题**: 复杂的元组解包和上下文管理器返回值
   - **示例**:
     ```python
     # _enter_nested 返回 tuple[Any, OrderedDict[str, NestedState], dict[str, Any], list[str]] | None
     self.scoped, self.states, self.events, self.prefix_path = self._next_scope
     ```
   - **当前方案**: `# type: ignore[assignment]`
   - **改进难度**: 🟢 低（改进类型注解即可）

3. **异步函数调用检查** (`func-returns-value`)
   - **位置**: `asyncio.py`
   - **问题**: await 调用的函数在类型系统中返回 Coroutine，但实际被设计为 fire-and-forget
   - **示例**:
     ```python
     await event_data.machine.callbacks(self.on_enter, event_data)
     # callbacks() 返回 Coroutine，但被设计为不返回值
     ```
   - **当前方案**: `# type: ignore[func-returns-value]`
   - **改进难度**: 🟢 低（改进返回类型注解）

#### C 类：实现细节（Implementation Details）

这类问题可以通过改进代码实现来解决。

1. **条件分支返回类型** (`no-any-return`)
   - **位置**: `asyncio.py`
   - **问题**: 条件分支返回不同类型的值
   - **示例**:
     ```python
     if inspect.isawaitable(res):
         result = await res
         return result == self.target
     return res == self.target
     # mypy 无法推导两个分支的返回类型相同
     ```
   - **当前方案**: `# type: ignore[no-any-return]`
   - **改进难度**: 🟢 低（重构逻辑结构）

2. **容器类型推导** (`union-attr`, `index`)
   - **位置**: `nesting.py`, `diagrams_*.py`
   - **问题**: 复杂的容器操作导致类型推导失败
   - **示例**:
     ```python
     state_tree = reduce(dict.get, machine.get_global_name(join=False), state_tree)
     # reduce 的返回类型无法准确推导
     ```
   - **当前方案**: `# type: ignore[union-attr]`, `# type: ignore[index]`
   - **改进难度**: 🟢 低（使用 cast 或改进类型注解）

## 3. 改进方案

### 3.1 短期方案（v1.x）

**目标**: 在不破坏 API 兼容性的前提下，减少 type ignore 数量 30-50%

#### 1. 使用 Protocol 显式声明动态属性接口

**适用问题**: 动态属性访问 (`attr-defined`, `union-attr`)

**实施方案**:

```python
# tfism/core.py
from typing import Protocol

class StateSeparator(Protocol):
    """具有 separator 属性的状态类"""
    separator: str

class NestedStateContainer(Protocol):
    """包含嵌套状态的容器接口"""
    events: dict[str, Any]
    states: OrderedDict[str, 'NestedState']

# 使用示例
def process_state(machine: Machine, state_cls: StateSeparator) -> None:
    sep = state_cls.separator  # 类型检查通过
```

**收益**: 减少约 20-30 个 type ignore

#### 2. 使用 TypeGuard 改进联合类型处理

**适用问题**: 联合类型属性访问 (`union-attr`)

**实施方案**:

```python
# tfism/extensions/nesting.py
from typing import TypeGuard, Union

def _is_nested_state(state: Union[State, NestedState]) -> TypeGuard[NestedState]:
    """类型守卫：判断是否为嵌套状态"""
    return hasattr(state, 'states') and hasattr(state, 'events')

def process(state: Union[State, NestedState]) -> None:
    if _is_nested_state(state):
        # 此时 state 被识别为 NestedState
        events = state.events  # 类型检查通过
        states = state.states  # 类型检查通过
```

**收益**: 减少约 10-20 个 type ignore

#### 3. 改进元组解包类型注解

**适用问题**: 元组解包类型推导 (`assignment`)

**实施方案**:

```python
# tfism/extensions/nesting.py
from typing import TypedDict, TypeAlias

# 定义明确的上下文类型
class StateContext(TypedDict):
    scoped: 'NestedState'
    states: OrderedDict[str, 'NestedState']
    events: dict[str, 'NestedEvent']
    prefix_path: list[str]

StateContextOrNone: TypeAlias = tuple[StateContext | None, ...]

# 使用
context: StateContext = self._next_scope  # 明确类型
self.scoped, self.states, self.events, self.prefix_path = context
```

**收益**: 减少约 10-15 个 type ignore

#### 4. 统一异步函数返回类型

**适用问题**: 异步函数调用检查 (`func-returns-value`)

**实施方案**:

```python
# tfism/extensions/asyncio.py
from typing import Coroutine, Any

# 将 callbacks 等函数的返回类型统一声明为 Coroutine[Any, Any, None]
async def callbacks(
    self,
    callbacks: CallbackList,
    event_data: EventData
) -> None:  # 改为 None
    """Execute callbacks asynchronously."""
    for func in callbacks:
        await self._callback(func, event_data)
    # 移除 return 语句或显式 return None
```

**收益**: 减少约 15 个 type ignore

### 3.2 中期方案（v1.5-v1.9）

**目标**: 引入泛型基类，部分解决异步/同步 LSP 违规，减少 type ignore 数量 50-70%

#### 1. 使用泛型分离同步/异步实现

**适用问题**: 异步/同步 LSP 违规 (`override`)

**实施方案**:

```python
# tfism/core.py
from typing import TypeVar, Generic, Callable, Awaitable

T = TypeVar('T', bool, Awaitable[bool])

class BaseMachine(Generic[T], ABC):
    """使用泛型参数 T 区分同步/异步状态机的基类"""

    @abstractmethod
    def dispatch(self, *args: Any, **kwargs: Any) -> T:
        """派发事件，返回类型取决于 T"""
        ...

# tfism/core.py (同步实现)
class SyncMachine(BaseMachine[bool]):
    """同步状态机"""
    def dispatch(self, *args: Any, **kwargs: Any) -> bool:
        # 同步实现
        ...

# tfism/extensions/asyncio.py (异步实现)
class AsyncMachine(BaseMachine[Awaitable[bool]]):
    """异步状态机"""
    async def dispatch(self, *args: Any, **kwargs: Any) -> bool:
        # 异步实现（自动包装为 Coroutine）
        ...
```

**优势**:
- 完全符合 LSP 原则
- 编译时类型安全
- 无需 `# type: ignore[override]`
- 更好的 IDE 支持和代码补全

**挑战**:
- 破坏向后兼容性（需要作为主要版本变更）
- 用户代码需要适配新 API
- 迁移成本高

**迁移策略**:
```python
# 提供兼容层，在 v1.x 中引入泛型基类，但保留旧 API
class Machine(BaseMachine[bool]):
    """兼容性包装器"""
    pass

class AsyncMachine(BaseMachine[Awaitable[bool]]):
    """兼容性包装器"""
    pass

# v2.0 中移除兼容层，直接使用泛型基类
```

**收益**: 减少约 40 个 type ignore（override）

#### 2. 使用 TypeVar bound 优化子类方法签名

**适用问题**: 子类方法签名扩展 (`override`, `arg-type`)

**实施方案**:

```python
# tfism/extensions/nesting.py
from typing import TypeVar, Union

S = TypeVar('S', bound=State)

class HierarchicalMachine(Machine):
    def set_state(
        self,
        state: Union[str, Enum, List[str], S],  # 使用 TypeVar bound
        model: Optional[Any] = None
    ) -> None:
        """设置状态，支持嵌套状态路径"""
        if isinstance(state, list):
            # 处理嵌套状态路径
            ...
        else:
            # 调用父类方法
            super().set_state(state, model)  # type: ignore[arg-type]

    def _add_model_to_state(self, state: S, model: Any) -> None:
        """添加模型到状态，使用 TypeVar bound 确保类型兼容性"""
        # state 被约束为 State 或其子类
        ...
```

**收益**: 减少约 10-15 个 type ignore

#### 3. 显式声明所有动态属性

**适用问题**: 动态属性访问 (`attr-defined`)

**实施方案**:

```python
# tfism/extensions/nesting.py
from dataclasses import dataclass, field
from typing import Any

@dataclass
class NestedState:
    """嵌套状态，显式声明所有属性"""
    name: str
    separator: str = field(default="_")
    events: dict[str, 'NestedEvent'] = field(default_factory=dict)
    states: OrderedDict[str, 'NestedState'] = field(default_factory=OrderedDict)
    initial: Union[str, List[str], 'NestedState', Enum, None] = None
    on_enter: CallbackList = field(default_factory=list)
    on_exit: CallbackList = field(default_factory=list)
    on_final: CallbackList = field(default_factory=list)
    _scope: list[str] = field(default_factory=list)

    # 其他现有方法和属性
    ...
```

**收益**: 减少约 15-20 个 type ignore

### 3.3 长期方案（v2.0）

**目标**: 完全重构类型系统，实现 100% 类型安全（无需 type ignore）

#### 1. 重新设计继承层次

**核心思想**: 将嵌套状态机和异步状态机设计为独立的类型，而非继承自基础机器

```python
# 新的架构设计
class Machine:
    """基础状态机"""
    ...

class HierarchicalMachine:
    """嵌套状态机，不继承自 Machine（组合优于继承）"""
    def __init__(self):
        self._machine = Machine()  # 组合而非继承
        # 嵌套状态机特定实现
        ...

class AsyncMachine:
    """异步状态机，不继承自 Machine"""
    def __init__(self):
        self._machine = Machine()  # 组合而非继承
        # 异步特定实现
        ...
```

**优势**:
- 完全消除 LSP 违规
- 每个类都有明确的类型契约
- 更好的代码组织和维护性
- 支持渐进式迁移

#### 2. 引入状态机构建器模式

**核心思想**: 使用构建器模式在运行时生成类型安全的状态机

```python
from typing import Protocol

class StateMachineBuilder(Protocol):
    """状态机构建器接口"""
    def add_state(self, name: str) -> 'StateMachineBuilder':
        ...

    def add_transition(
        self,
        trigger: str,
        source: str,
        dest: str
    ) -> 'StateMachineBuilder':
        ...

    def build(self) -> Machine:
        ...

# 类型安全的构建器
builder = StateMachineBuilder()
machine = (builder
    .add_state("idle")
    .add_state("running")
    .add_transition("start", "idle", "running")
    .build())

# 生成的 machine 具有类型安全的方法
machine.start()  # IDE 自动补全和类型检查
```

**优势**:
- 编译时类型检查（自动生成的触发方法）
- 更好的 IDE 支持
- 消除运行时动态属性

#### 3. 使用 Pydantic 或 msgspec 进行运行时类型验证

**核心思想**: 结合静态类型检查和运行时类型验证

```python
from pydantic import BaseModel, Field
from typing import Callable, Any

class StateConfig(BaseModel):
    """状态配置，带运行时类型验证"""
    name: str
    on_enter: list[Callable[..., Any]] = Field(default_factory=list)
    on_exit: list[Callable[..., Any]] = Field(default_factory=list)
    ignore_invalid_triggers: bool = False
    final: bool = False

class MachineConfig(BaseModel):
    """状态机配置"""
    states: list[StateConfig]
    transitions: list[TransitionConfig]
    send_event: bool = False
    auto_transitions: bool = True

# 使用配置创建状态机
config = MachineConfig(...)
machine = Machine.from_config(config)  # 类型安全的构建方法
```

**优势**:
- 运行时类型验证保证数据完整性
- 自动生成 JSON Schema
- 更好的错误消息

## 4. 实施路线图

### 4.1 Phase 1: 类型清理（1-2 个月）

**目标**: 减少 30-50% 的 type ignore

- [ ] 为动态属性添加 Protocol 定义
- [ ] 使用 TypeGuard 处理联合类型
- [ ] 改进元组解包类型注解
- [ ] 统一异步函数返回类型
- [ ] 添加更详细的 type: ignore 错误码注释

**成功指标**:
- type ignore 数量 < 150
- 所有 type ignore 都有明确的错误码和 TODO 注释
- mypy strict 检查通过

### 4.2 Phase 2: 架构优化（3-4 个月）

**目标**: 减少 50-70% 的 type ignore

- [ ] 引入泛型基类（作为 opt-in 特性）
- [ ] 使用 TypeVar bound 优化子类方法
- [ ] 显式声明所有动态属性
- [ ] 重构复杂类型推导代码
- [ ] 添加更多单元测试覆盖类型边界

**成功指标**:
- type ignore 数量 < 100
- 核心模块（core.py, asyncio.py, nesting.py）type ignore < 50
- 新功能实现时零 type ignore

### 4.3 Phase 3: 架构重构（v2.0，6-12 个月）

**目标**: 完全消除 type ignore，实现 100% 类型安全

- [ ] 重新设计继承层次
- [ ] 引入状态机构建器模式
- [ ] 集成 Pydantic/msgspec 运行时验证
- [ ] 提供迁移指南和兼容层
- [ ] 更新文档和示例

**成功指标**:
- type ignore 数量 = 0
- 所有公共 API 完全类型安全
- 向后兼容性迁移路径清晰

## 5. 最佳实践

### 5.1 Type Ignore 使用规范

```python
# ✅ 好的做法：包含错误码和解释
func(x)  # type: ignore[arg-type]  # Architectural limitation: async override of sync method

# ✅ 好的做法：添加 TODO
async def method(self) -> None:  # type: ignore[override]
    # TODO: Generic-based async/sync separation (planned for v2.0)
    ...

# ❌ 坏的做法：无错误码
func(x)  # type: ignore

# ❌ 坏的做法：无解释
async def method(self) -> None:  # type: ignore[override]
    ...
```

### 5.2 类型注解规范

```python
# ✅ 好的做法：使用 TypeAlias 提高可读性
from typing import TypeAlias

StateName: TypeAlias = str | Enum
Callback: TypeAlias = Callable[..., Any]

def process(name: StateName, callback: Callback) -> None:
    ...

# ❌ 坏的做法：内联复杂类型
def process(name: str | Enum, callback: Callable[..., Any]) -> None:
    ...
```

### 5.3 循环导入处理

```python
# ✅ 好的做法：使用 TYPE_CHECKING
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Machine

class MyState:
    def __init__(self, machine: "Machine") -> None:  # 使用字符串前向引用
        ...

# ❌ 坏的做法：运行时导入
class MyState:
    def __init__(self, machine: "Machine") -> None:
        ...
        from .core import Machine  # 运行时导入
```

## 6. 工具和配置

### 6.1 Mypy 配置

```ini
# mypy.ini
[mypy]
python_version = 3.11
strict = True
warn_unused_ignores = True  # 检测无用的 type: ignore
show_error_codes = True  # 显示错误码
show_column_numbers = True
pretty = True

# 每个模块的特定配置
[mypy-tfism.extensions.asyncio]
disable_error_code = ["override"]  # 临时禁用 override 错误

[mypy-tfism.extensions.nesting]
disable_error_code = ["union-attr"]  # 临时禁用 union-attr 错误
```

### 6.2 CI/CD 集成

```yaml
# .github/workflows/typecheck.yml
name: Type Check

on: [push, pull_request]

jobs:
  mypy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements-dev.txt
      - run: mypy --config-file mypy.ini --strict tfism
      - run: |
          # 检查 type ignore 数量
          count=$(grep -r "# type: ignore" tfism/ | wc -l)
          echo "Current type ignore count: $count"
          if [ $count -gt 150 ]; then
            echo "Too many type ignores! Current: $count, Target: <= 150"
            exit 1
          fi
```

### 6.3 开发工作流

```bash
# 开发时运行类型检查
uv run mypy --config-file mypy.ini --strict tfism

# 监视模式（需要 mypy-watch）
uv run mypy --config-file mypy.ini --strict tfism --watch

# 提交前检查
uv run mypy --config-file mypy.ini --strict tfism && uv run pytest

# 统计 type ignore 数量
grep -r "# type: ignore" tfism/ | wc -l

# 按错误类型统计
grep -r "# type: ignore" tfism/ | grep -o "\[.*\]" | sort | uniq -c
```

## 7. 相关资源

- [Mypy 文档 - 类型忽略最佳实践](https://mypy.readthedocs.io/en/stable/type_inference_and_annotations.html)
- [PEP 544 - Protocol: Structural Subtyping (Static Duck Typing)](https://peps.python.org/pep-0544/)
- [PEP 612 - Parameter Specification Variables](https://peps.python.org/pep-0612/)
- [PEP 647 - TypeGuard](https://peps.python.org/pep-0647/)
- [Python 类型系统演进路线图](https://github.com/python/typing/issues/994)
- [Effective Python, 3rd Edition - Chapter 3: Type Hinting](https://effectivepython.com/)

## 8. 总结

当前 `tfism` 项目已达到良好的类型安全水平：
- ✅ 通过 mypy strict 检查
- ✅ 100% 类型注解覆盖
- ✅ 所有功能测试通过

但仍有改进空间：
- 🔧 243 个 type ignore 需要逐步减少
- 🏗️ 架构层问题需要重构解决
- 📈 可以通过渐进式改进提升类型安全性

通过实施本文档提出的改进方案，预期在 v2.0 版本实现完全类型安全的状态机框架。
