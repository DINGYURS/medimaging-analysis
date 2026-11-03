import asyncio
from app.core.config import settings

class GPUQueueGate:
    def __init__(self, max_concurrent: int):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.max_concurrent = max_concurrent
        self.active_tasks = 0
        self.queued_tasks = 0

    async def acquire(self):
        self.queued_tasks += 1
        await self.semaphore.acquire()
        self.queued_tasks -= 1
        self.active_tasks += 1

    def release(self):
        self.active_tasks = max(0, self.active_tasks - 1)
        self.semaphore.release()

    def get_stats(self):
        return {
            "max_concurrent": self.max_concurrent,
            "active_tasks": self.active_tasks,
            "queued_tasks": self.queued_tasks
        }

# Global GPU gate instance initialized with configured max tasks
gpu_gate = GPUQueueGate(max_concurrent=settings.MAX_GPU_CONCURRENT_TASKS)
