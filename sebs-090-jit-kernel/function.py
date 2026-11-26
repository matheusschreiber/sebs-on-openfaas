from time import perf_counter_ns


def kernel(iterations: int, seed: int) -> int:
    """
    CPU-bound integer workload designed to exercise PyPy's JIT.

    Returns a deterministic 32-bit checksum.
    """
    x = seed & 0xFFFFFFFF
    checksum = 0

    for i in range(iterations):
        x = (x * 1664525 + 1013904223) & 0xFFFFFFFF
        checksum = (checksum + (x ^ i)) & 0xFFFFFFFF

    return checksum


def handler(event):
    iterations = event.get('iterations')
    seed = event.get('seed')

    # Start timing: measure only the computational kernel.
    start = perf_counter_ns()
    result = kernel(iterations, seed)
    elapsed_ns = perf_counter_ns() - start

    return {
        'result': result,
        'measurement': {
            'compute_time': elapsed_ns / 1e9,
            'compute_time_ns': elapsed_ns
        }
    }