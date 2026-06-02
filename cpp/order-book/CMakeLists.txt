cmake_minimum_required(VERSION 3.16)
project(order_book_simulator LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include_directories(include)

add_library(orderbook_core
    src/order_book.cpp
    src/matching_engine.cpp
)

add_executable(order_book_demo src/main.cpp)
target_link_libraries(order_book_demo orderbook_core)

# ---------------------------------------------------------------------------
# Testing: GoogleTest via FetchContent (no system install required).
# ---------------------------------------------------------------------------
enable_testing()

option(BUILD_TESTING "Build the unit tests" ON)

if(BUILD_TESTING)
    include(FetchContent)
    FetchContent_Declare(
        googletest
        GIT_REPOSITORY https://github.com/google/googletest.git
        GIT_TAG v1.15.2
        GIT_SHALLOW TRUE
    )
    # Don't override the parent project's compiler/linker settings (Windows).
    set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
    FetchContent_MakeAvailable(googletest)

    add_executable(orderbook_tests tests/test_order_book.cpp)
    target_link_libraries(orderbook_tests PRIVATE orderbook_core GTest::gtest_main)

    include(GoogleTest)
    gtest_discover_tests(orderbook_tests)
endif()

# ---------------------------------------------------------------------------
# Python bindings: pybind11 via FetchContent (no system/pip install required).
# Produces an importable extension module `_orderbook` placed into
# python/orderbook/ so `import orderbook` works from the python/ dir.
# Guarded by BUILD_PYTHON_BINDINGS so the C++ demo + GoogleTest targets still
# build if a Python interpreter/headers aren't available.
# ---------------------------------------------------------------------------
option(BUILD_PYTHON_BINDINGS "Build the pybind11 Python module" ON)

if(BUILD_PYTHON_BINDINGS)
    include(FetchContent)
    FetchContent_Declare(
        pybind11
        GIT_REPOSITORY https://github.com/pybind/pybind11.git
        GIT_TAG v2.13.6
        GIT_SHALLOW TRUE
    )
    FetchContent_MakeAvailable(pybind11)

    pybind11_add_module(_orderbook src/bindings.cpp src/order_book.cpp src/matching_engine.cpp)
    target_include_directories(_orderbook PRIVATE include)

    # Drop the compiled module next to the Python re-export package so
    # `import orderbook` resolves it without any install step.
    set_target_properties(_orderbook PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/python/orderbook
    )
endif()
