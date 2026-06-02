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
