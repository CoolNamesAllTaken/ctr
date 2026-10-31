set(srcs "core/usb_eth_core.c"
         "core/usb_eth_mac.c"
         "core/usb_eth_phy.c"
         "core/usb_eth_registry.c"
         "core/usb_eth_util.c")
set(priv_includes "private_include")

if(CONFIG_USB_ETH_DRV_RTL815X)
    list(APPEND srcs "drivers/rtl815x/rtl815x.c" "drivers/rtl815x/rtl815x_proto.c")
    list(APPEND priv_includes "drivers/rtl815x")
endif()

idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS "include"
                       PRIV_INCLUDE_DIRS ${priv_includes}
                       REQUIRES esp_eth usb
                       PRIV_REQUIRES esp_hw_support esp_timer log)

# Keep the component warning-free: any warning fails the build.
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wshadow -Wno-unused-parameter)
