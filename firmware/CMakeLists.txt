cmake_minimum_required(VERSION 3.28.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(ads131m08)

target_sources(app PRIVATE 
    src/main.c
    src/ads131m08.c
    src/hbridge.c
    src/telemetry.c
)