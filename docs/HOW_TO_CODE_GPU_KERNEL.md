# How to Code GPU Kernels

This guide provides a comprehensive overview of how to code GPU kernels, covering foundational concepts, popular frameworks, memory management, and performance considerations. GPUs (Graphics Processing Units) are specialized electronic circuits designed to rapidly manipulate and alter memory to accelerate the creation of images in a frame buffer intended for output to a display device. Modern GPUs are also highly efficient at processing large blocks of data in parallel, making them invaluable for scientific computing, machine learning, and other computationally intensive tasks.

## Why GPU Computing?

GPUs excel at tasks that can be broken down into many smaller, independent computations that can be performed simultaneously. This is known as "parallel computing." Unlike CPUs, which are optimized for sequential task processing with a few powerful cores, GPUs are designed with thousands of smaller, simpler cores that can handle massive parallelism.

## CPU vs. GPU Programming

Understanding the architectural and programming model differences is crucial for effective GPU programming.

### Architectural Differences

*   **CPU (Central Processing Unit):** Few powerful cores, optimized for latency, complex control logic, large caches. Best for sequential tasks, branch-heavy code, and general-purpose computing.
*   **GPU (Graphics Processing Unit):** Many smaller, simpler cores, optimized for throughput, simpler control logic, smaller caches (per core). Best for highly parallelizable tasks with data-parallel characteristics.

### Programming Model Differences

*   **CPU Programming:** Typically involves managing threads and processes with OS-level APIs (e.g., pthreads, OpenMP, TBB) or high-level abstractions, often with shared memory models.
*   **GPU Programming:** Involves writing "kernels" that execute on the device (GPU) and managing memory explicitly between the host (CPU) and device. The programming model is inherently parallel, thinking in terms of thousands of threads.

## Choosing a Framework

Several frameworks enable GPU programming. The choice often depends on hardware, ecosystem, and specific project requirements.

*   **CUDA (Compute Unified Device Architecture):** NVIDIA's proprietary parallel computing platform and API model. It's the most mature and widely adopted for NVIDIA GPUs, offering robust tools, libraries, and extensive documentation. Written in C/C++ with CUDA extensions.
*   **OpenCL (Open Computing Language):** An open standard for parallel programming across heterogeneous platforms consisting of CPUs, GPUs, FPGAs, and other processors. It provides vendor independence but can be more verbose than CUDA. Supports C99 as its kernel language.
*   **HIP (Heterogeneous-Compute Interface for Portability):** A C++ runtime API and kernel language designed to allow developers to port their CUDA applications to run on AMD GPUs (and other devices supporting ROCm) with minimal code changes. It provides a path to create portable applications for both NVIDIA and AMD GPUs.
*   **SYCL (pronounced "sickle"):** A higher-level programming model built on top of OpenCL, designed to provide a single-source C++ programming approach for heterogeneous computing, aiming for better productivity and portability.

For this guide, we will primarily use CUDA examples due to its prevalence and clarity.

## Essential GPU Concepts

GPU programming introduces a hierarchical execution model.

*   **Threads:** The smallest unit of execution on the GPU. Each thread executes the kernel function independently. Thousands or millions of threads can run concurrently.
*   **Blocks (Thread Blocks):** A group of threads that can communicate with each other and synchronize their execution using shared memory and barrier synchronization. Threads within a block are typically scheduled on the same Streaming Multiprocessor (SM) or Compute Unit (CU). Blocks are organized in up to three dimensions.
*   **Grids (Grid of Blocks):** A collection of thread blocks. A kernel is executed as a grid of blocks. Each block in the grid is independent and can be scheduled in any order. Grids are also organized in up to three dimensions.
*   **Warps/Wavefronts:** A group of 32 (CUDA) or 64 (AMD GCN/RDNA) threads that execute the same instruction in lock-step. This is the fundamental unit of scheduling on an SM/CU. If threads within a warp take divergent execution paths (e.g., due to an `if-else` statement), the hardware serially executes each path, leading to performance degradation.

### Kernel Functions

A kernel is a function written in a GPU programming language (e.g., CUDA C++) that is executed on the device. It is identified by a special declaration (e.g., `__global__` in CUDA).

```cpp
// CUDA example
__global__ void myKernel(float* deviceData, int size) {
    // Kernel logic here
}
```

## GPU Memory Types

GPUs have a complex memory hierarchy, each with different scopes, lifetimes, and performance characteristics. Understanding these is vital for optimizing kernel performance.

*   **Global Memory:**
    *   **Scope:** Accessible by all threads in all blocks, and by the host (CPU).
    *   **Lifetime:** From allocation to deallocation by the host.
    *   **Performance:** High latency, low bandwidth relative to other on-chip memories. Resides off-chip (DRAM).
    *   **Use Cases:** Main data storage for input/output data, large arrays.
*   **Shared Memory:**
    *   **Scope:** Accessible by all threads within the same thread block.
    *   **Lifetime:** Per thread block. Created when a block starts, destroyed when it finishes.
    *   **Performance:** Extremely fast, on-chip memory. Much lower latency and higher bandwidth than global memory.
    *   **Use Cases:** Thread communication within a block, caching frequently accessed global memory data, reducing global memory accesses.
*   **Constant Memory:**
    *   **Scope:** Accessible by all threads in all blocks. Read-only.
    *   **Lifetime:** From allocation to deallocation by the host.
    *   **Performance:** Fast if all threads in a warp access the same address. Cached.
    *   **Use Cases:** Storing small, frequently accessed, read-only data that is constant across kernel execution (e.g., coefficients, lookup tables).
*   **Local Memory:**
    *   **Scope:** Private to a single thread.
    *   **Lifetime:** Per thread.
    *   **Performance:** Resides in global memory, so it has high latency. Used when a thread's private data spills out of registers.
    *   **Use Cases:** Large local arrays, structures, or variables that cannot fit into registers.
*   **Register Memory:**
    *   **Scope:** Private to a single thread.
    *   **Lifetime:** Per thread.
    *   **Performance:** Fastest memory. Directly accessible by the ALU.
    *   **Use Cases:** Scalar variables, small arrays within a kernel function.

## Data Transfer: Host to Device & Device to Host

Data must be explicitly transferred between the host (CPU's memory) and the device (GPU's memory). This is typically a bottleneck, so minimizing transfers is crucial.

1.  **Allocate Memory on Device:** Reserve space in the GPU's global memory.
2.  **Copy Data from Host to Device:** Transfer input data from CPU RAM to GPU DRAM.
3.  **Launch Kernel:** Execute the kernel on the GPU.
4.  **Copy Data from Device to Host (Optional):** Transfer results from GPU DRAM back to CPU RAM.
5.  **Deallocate Memory on Device:** Free the reserved space on the GPU.

### Example (CUDA):

```cpp
// On Host (CPU)
float* host_A; // Host-side pointer
float* device_A; // Device-side pointer

// 1. Allocate host memory
host_A = (float*)malloc(size * sizeof(float));
// ... Initialize host_A ...

// 2. Allocate device memory
cudaMalloc((void**)&device_A, size * sizeof(float));

// 3. Copy data from host to device
cudaMemcpy(device_A, host_A, size * sizeof(float), cudaMemcpyHostToDevice);

// 4. Launch kernel (covered in next section)
// myKernel<<<gridDim, blockDim>>>(device_A, size);

// 5. Copy results back from device to host (if device_A was output)
// cudaMemcpy(host_A, device_A, size * sizeof(float), cudaMemcpyDeviceToHost);

// 6. Free device memory
cudaFree(device_A);

// 7. Free host memory
free(host_A);
```

## Basic Kernel Structure and Execution

A GPU kernel is a function that runs on the GPU. To launch it, you need to specify the execution configuration (grid and block dimensions).

### Kernel Definition (CUDA)

```cpp
__global__ void vectorAdd(float* A, float* B, float* C, int N) {
    // Calculate global thread index
    int i = blockIdx.x * blockDim.x + threadIdx.x;

    if (i < N) {
        C[i] = A[i] + B[i];
    }
}
```

*   `__global__`: Specifies that this function is a kernel that runs on the device and can be called from the host.
*   `blockIdx.x`, `blockDim.x`, `threadIdx.x`: Built-in CUDA variables providing the current block's index, the dimensions of the block, and the current thread's index within its block, respectively. These are used to uniquely identify each thread's position in the overall grid.

### Kernel Launch Configuration (CUDA)

To launch a kernel, you use the `<<<...>>>` syntax:

```cpp
// On Host (CPU)
int N = 1024; // Vector size
// ... memory allocations and copies for A, B, C ...

// Configure grid and block dimensions
int threadsPerBlock = 256;
int blocksPerGrid = (N + threadsPerBlock - 1) / threadsPerBlock;

// Launch the kernel
vectorAdd<<<blocksPerGrid, threadsPerBlock>>>(device_A, device_B, device_C, N);

// Synchronize to ensure kernel completes before host proceeds (important for results)
cudaDeviceSynchronize();
```

The `blocksPerGrid` and `threadsPerBlock` determine how many parallel threads will execute the kernel. For 1D problems, a common pattern is to have `threadsPerBlock` around 128, 256, or 512 (multiples of warp size) and calculate `blocksPerGrid` to cover all elements.

## Synchronization

Synchronization is critical to ensure correct execution order and data visibility.

*   **`__syncthreads()` (CUDA):** A barrier synchronization primitive within a thread block. All threads in the block must reach this point before any thread can proceed past it. Essential when threads in a block share data via shared memory.
*   **Stream Synchronization (CUDA `cudaStreamSynchronize()`):** Synchronizes a specific stream, waiting for all commands issued to that stream to complete.
*   **Device Synchronization (CUDA `cudaDeviceSynchronize()`):** Waits for all previously issued CUDA calls (across all streams) to complete on the device. Useful for ensuring all kernels finish before reading results back to the host, but can hide concurrency.

## Debugging GPU Kernels

Debugging GPU kernels can be challenging due to their parallel nature.

*   **Tools:**
    *   **CUDA-GDB:** A GDB extension for debugging CUDA applications on Linux, allowing breakpoints, single-stepping, and inspecting variables on the device.
    *   **NVIDIA Nsight Compute:** A powerful interactive kernel profiler for NVIDIA GPUs, providing detailed performance metrics, memory access patterns, and source-level analysis.
    *   **NVIDIA Nsight Systems:** A system-wide profiler to visualize application activities, including CPU threads, GPU kernels, memory transfers, and synchronization events.
*   **Common Issues:**
    *   **Index out of bounds:** Incorrect calculation of `i` can lead to memory corruption or crashes.
    *   **Race conditions:** Threads accessing or modifying shared data without proper synchronization.
    *   **Memory errors:** Incorrect `cudaMalloc`/`cudaFree` or `cudaMemcpy` usage.
    *   **Thread divergence:** `if-else` branches within a warp causing serialization and performance degradation.
    *   **Stack overflow:** Recursion or large local variables causing a thread's private stack to exceed limits.
    *   **Uninitialized memory:** Using device memory before copying data to it.

## Performance Considerations

Optimizing GPU kernel performance involves understanding the underlying hardware and memory hierarchy.

*   **Memory Access Patterns:**
    *   **Coalesced Memory Access:** When threads within a warp access contiguous memory locations. This is the most efficient global memory access pattern.
    *   **Shared Memory Banking:** Shared memory is divided into banks. Avoid "bank conflicts" where multiple threads in a warp try to access different data in the same bank simultaneously, as this leads to serialization.
*   **Occupancy:** The number of active warps/wavefronts on an SM/CU at any given time. Higher occupancy can hide latency but too high can lead to register/shared memory pressure.
*   **Compute-to-Global Memory Access Ratio:** Strive to do more computation per global memory access. This means re-using data in faster on-chip memories (registers, shared memory) as much as possible.
*   **Branch Divergence:** Minimize `if-else` statements or loops whose execution paths differ among threads within the same warp.
*   **Resource Management:** Carefully manage register usage and shared memory allocation. Excessive usage can limit occupancy.
*   **Asynchronous Operations & Streams:** Use CUDA streams to overlap computation with memory transfers or overlap different kernel executions, improving overall throughput.
*   **Algorithm Choice:** Sometimes, a completely different algorithm designed for parallelism will outperform a parallelized sequential algorithm.

By mastering these concepts, you can effectively harness the power of GPUs to accelerate your applications. Start with simple kernels, profile them, and iteratively optimize based on the insights gained from profiling tools.