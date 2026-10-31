idf_component_register(SRCS "net_ota.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_http_server
                       PRIV_REQUIRES app_update bootloader_support esp_app_format esp_timer esp_system log mdns)

target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wno-error=cpp -Wno-unused-parameter)
