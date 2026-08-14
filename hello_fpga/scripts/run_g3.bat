@echo off
setlocal

set "BUILD_TCL=D:\fpgaproject\hello_fpga\scripts\build.tcl"
set "BUILD_LOG=D:\fpgaproject\hello_fpga\scripts\build.log"

echo.
echo ===== G3: hello_fpga Build =====
echo Loading Vivado 2023.1 environment...
call D:\Xilinx\Vivado\2023.1\settings64.bat > NUL 2>&1

echo Running build.tcl ...
echo Log: %BUILD_LOG%
echo.

call vivado -mode batch -source "%BUILD_TCL%" > "%BUILD_LOG%" 2>&1

echo.
echo ===== Build Complete =====
echo Exit code: %ERRORLEVEL%
echo.
echo Reports: D:\fpgaproject\hello_fpga\reports\
echo Bitfile: D:\fpgaproject\hello_fpga\output\hello_fpga.bit
echo.
