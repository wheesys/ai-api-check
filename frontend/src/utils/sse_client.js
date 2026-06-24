// SSE 客户端：订阅任务进度事件流。
// 后端以 `event: <type>\ndata: <json>\n\n` 推送；EventSource 按 type 分发到回调。
import { tasksApi } from '../api/tasks'

const EVENT_TYPES = [
  'task.started',
  'probe.completed',
  'task.scored',
  'task.completed',
  'task.failed',
  'task.canceled'
]
const TERMINAL = new Set(['task.completed', 'task.failed', 'task.canceled'])

// 订阅任务事件；handlers: { onEvent(type, data), onDone(type) }。返回关闭函数。
export function subscribeTaskEvents(taskId, handlers = {}) {
  const source = new EventSource(tasksApi.eventsUrl(taskId))

  const dispatch = (type) => (event) => {
    let data = {}
    try {
      data = JSON.parse(event.data)
    } catch {
      data = {}
    }
    handlers.onEvent?.(type, data)
    if (TERMINAL.has(type)) {
      source.close()
      handlers.onDone?.(type, data)
    }
  }

  EVENT_TYPES.forEach((type) => source.addEventListener(type, dispatch(type)))
  source.onerror = () => {
    source.close()
    handlers.onError?.()
  }

  return () => source.close()
}
