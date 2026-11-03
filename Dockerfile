ARG baseimage="ubuntu:24.04"
FROM ${baseimage} AS build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    cmake \
    ninja-build \
    libgmp-dev \
    libboost-all-dev \
    libz3-dev \
    python3 \
    python3-pip \
    llvm-14 \
    llvm-14-dev \
    llvm-14-tools \
    clang-14 \
    libgtest-dev \
    libgmock-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set up LLVM environment variables
ENV LLVM_CONFIG=/usr/bin/llvm-config-14
ENV LLVM_BUILD_PATH=/usr/lib/llvm-14/lib/cmake/llvm
ENV LLVM_DIR=/usr/lib/llvm-14/lib/cmake/llvm
ENV Z3_DIR=/usr

# Set working directory
WORKDIR /lotus

# Copy source code (.dockerignore drops build trees, .git and benchmark bitcode)
COPY . .

# Create build directory and configure
RUN mkdir -p build && cd build && \
    cmake -G Ninja -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_INSTALL_PREFIX=/usr/local \
          -DLLVM_BUILD_PATH=${LLVM_BUILD_PATH} \
          -DLLVM_CONFIG_PATH=${LLVM_CONFIG} \
          -DLLVM_DIR=${LLVM_DIR} \
          -DZ3_DIR=${Z3_DIR} \
          ..

# Build and install
RUN cd build && ninja -j$(nproc) && ninja install

# UseTraceSSA evaluation: the three tools the harness runs, in build/bin, where
# scripts/evaluate_usetracessa.py looks by default.
RUN mkdir -p /opt/lotus/build/bin && \
    cp build/bin/lotus-ir-usetracessa build/bin/lotus-ir-ufg build/bin/lotus-check \
       /opt/lotus/build/bin/ && \
    cp -r config scripts /opt/lotus/

# Runtime stage - minimal image with only runtime dependencies
FROM ${baseimage} AS runtime

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgmp10 \
    libboost-system1.83.0 \
    libboost-filesystem1.83.0 \
    libboost-program-options1.83.0 \
    libboost-thread1.83.0 \
    libboost-chrono1.83.0 \
    libboost-date-time1.83.0 \
    libz3-4 \
    python3 \
    llvm-14-runtime \
    libllvm14 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed binaries and libraries from build stage
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build /usr/local/lib /usr/local/lib

# UseTraceSSA evaluation harness. Tools run from the Lotus root so that
# config/*.spec are found; mount the benchmark bitcode (not redistributed) at
# /opt/lotus/benchmarks, e.g.
#   docker run -v $PWD/benchmarks:/opt/lotus/benchmarks lotus \
#     python3 /opt/lotus/scripts/evaluate_usetracessa.py --preset smoke \
#       --manifest /opt/lotus/scripts/manifests/spec-paper.json
COPY --from=build /opt/lotus /opt/lotus
ARG LOTUS_COMMIT=""
ENV LOTUS_COMMIT=${LOTUS_COMMIT}
ENV LOTUS_BIN_DIR=/opt/lotus/build/bin
ENV LOTUS_BENCHMARKS_ROOT=/opt/lotus

# Set library path
ENV LD_LIBRARY_PATH=/usr/local/lib:${LD_LIBRARY_PATH}
ENV PATH=/usr/local/bin:${PATH}

# Set working directory
WORKDIR /workspace