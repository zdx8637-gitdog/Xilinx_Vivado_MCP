/* =====================================================================
 * ad7606c_j11.xdc  —  AD7606C-16(凌智 16Bit@1MSPS_ADC V1.3) on ALINX AX7020 J11
 * 映射已封板（用户确认）：字母对 = 模块 J2 行序直连
 *   用户确认: (A|B)=OSI1|OSI0  (C|D)=SER|OSI2  (E|F)=CONV|STBY, 其余对同规律, 用户确认映射正确
 * 来源: ALINX 官方教程 §3.7.5 (J11 引脚表) + AN706 官方 XDC + 凌智手册/原理图 §3.4
 * 电平: LVCMOS33 (J11 全部 IO 在 BANK35=3.3V)
 * ===================================================================== */
set_property IOSTANDARD LVCMOS33 [get_ports {adc_os[*] adc_ser adc_conv adc_stby adc_reset adc_wr adc_cs adc_rd adc_frstd adc_busy adc_db[*]}]

# 控制/状态信号  (字母 -> J11 PIN -> FPGA 脚)
set_property PACKAGE_PIN F17 [get_ports {adc_os[1]}]   # A  OSI1  PIN3
set_property PACKAGE_PIN F16 [get_ports {adc_os[0]}]   # B  OSI0  PIN4
set_property PACKAGE_PIN F20 [get_ports adc_ser]       # C  SER   PIN5
set_property PACKAGE_PIN F19 [get_ports {adc_os[2]}]   # D  OSI2  PIN6
set_property PACKAGE_PIN G20 [get_ports adc_conv]      # E  CONV  PIN7
set_property PACKAGE_PIN G19 [get_ports adc_stby]      # F  STBY  PIN8  (正常工作需高)
set_property PACKAGE_PIN H18 [get_ports adc_reset]     # G  REST  PIN9
set_property PACKAGE_PIN J18 [get_ports adc_wr]        # H  WR    PIN10 (硬件模式可悬空/置高)
set_property PACKAGE_PIN L20 [get_ports adc_cs]        # I  CS_N  PIN11
set_property PACKAGE_PIN L19 [get_ports adc_rd]        # J  RD/CK PIN12
set_property PACKAGE_PIN M20 [get_ports adc_frstd]     # K  FR_D  PIN13
set_property PACKAGE_PIN M19 [get_ports adc_busy]      # L  BUSY  PIN14

# 并行数据总线 16 位  (每对字母 = (奇DB | 偶DB), 直连)
set_property PACKAGE_PIN K18 [get_ports {adc_db[1]} ]   # M  DB1  PIN15
set_property PACKAGE_PIN K17 [get_ports {adc_db[0]} ]   # N  DB0  PIN16
set_property PACKAGE_PIN J19 [get_ports {adc_db[3]} ]   # O  DB3  PIN17
set_property PACKAGE_PIN K19 [get_ports {adc_db[2]} ]   # P  DB2  PIN18
set_property PACKAGE_PIN H20 [get_ports {adc_db[5]} ]   # Q  DB5  PIN19
set_property PACKAGE_PIN J20 [get_ports {adc_db[4]} ]   # R  DB4  PIN20
set_property PACKAGE_PIN L17 [get_ports {adc_db[7]} ]   # S  DB7  PIN21
set_property PACKAGE_PIN L16 [get_ports {adc_db[6]} ]   # T  DB6  PIN22
set_property PACKAGE_PIN M18 [get_ports {adc_db[9]} ]   # U  DB9  PIN23
set_property PACKAGE_PIN M17 [get_ports {adc_db[8]} ]   # V  DB8  PIN24
set_property PACKAGE_PIN D20 [get_ports {adc_db[11]}]   # W  DB11 PIN25
set_property PACKAGE_PIN D19 [get_ports {adc_db[10]}]   # X  DB10 PIN26
set_property PACKAGE_PIN E19 [get_ports {adc_db[13]}]   # Y  DB13 PIN27
set_property PACKAGE_PIN E18 [get_ports {adc_db[12]}]   # Z  DB12 PIN28
set_property PACKAGE_PIN G18 [get_ports {adc_db[15]}]   # a  DB15 PIN29
set_property PACKAGE_PIN G17 [get_ports {adc_db[14]}]   # b  DB14 PIN30

# 空闲(未定义): c,d,e,f,g,h = PIN31..36 = H17,H16,G15,H15,J14,K14
# 电源: PIN1 GND / PIN2 +5V ; PIN37/38 GND ; PIN39/40 +3.3V(转接板标 NC)
