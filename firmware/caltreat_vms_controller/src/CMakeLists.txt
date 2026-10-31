idf_component_register(
    SRCS "main.cpp" "vms_core.cpp" "net.c" "feed.cpp"
    INCLUDE_DIRS "../include"
    REQUIRES esp_driver_uart esp_timer ESP32-HUB75-MatrixPanel-I2S-DMA
             app_update esp_app_format esp_eth esp_netif esp_event esp_http_client json mbedtls usb usb_eth net_ota mdns
)
