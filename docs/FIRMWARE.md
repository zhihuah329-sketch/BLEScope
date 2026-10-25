# STM32 参数接入

`firmware/DebugLink.c/.h` 是 STM32F1 标准外设库示例，可移植到其他平台。
它提供有边界的接收环形缓冲、参数注册、范围检查和ACK。参数只修改RAM。

将两个文件加入工程，用**实际参与算法的变量**注册参数，不要另建无效副本：

```c
#include "DebugLink.h"

volatile float Alpha = 0.02f;

static const DebugLinkParam Parameters[] = {
    {"filter.alpha", &Alpha, 0.0f, 1.0f}
};

static uint8_t CanWriteParameters(void)
{
    /* 台架示例。实际飞控应根据解锁/运行状态决定是否允许。 */
    return 1;
}

/* 初始化，在开启串口接收之前： */
DebugLink_Init(Parameters, sizeof(Parameters) / sizeof(Parameters[0]),
               Serial_SendByte, CanWriteParameters);
Serial_Init();

/* 主循环中经常调用： */
DebugLink_Task();
```

已有PID控制器时再注册实际的 `Kp/Ki/Kd`，范围根据控制对象确定。
软件里的参数名称不是可直接用于电机的推荐增益。

把现有USART1接收处理替换为以下方式，工程里只保留一个中断函数：

```c
void USART1_IRQHandler(void)
{
    if (USART_GetITStatus(USART1, USART_IT_RXNE) == SET)
        DebugLink_OnByte((uint8_t)USART_ReceiveData(USART1));
}
```

不要再同时使用另一份 `Serial_RxPacket` 解析器消费同一接收字节。
命令解析、发送ACK都在主循环执行，不放在接收中断中。

注册名最长32字节，不含逗号或方括号；上下限必须有限且最小值不大于最大值。
`CanWriteParameters` 在短临界区内执行，必须快速返回。
示例依赖 `stm32f10x.h`、CMSIS临界区函数和支持浮点格式化的C库。
更换平台时需适配临界区与UART发送接口，并在完整工程中编译验证。

可继续发送已有遥测：

```c
Serial_Printf("[plot,%f,%f]", AngleAcc, AngleEstimate);
```

建议加入设备时间戳，便于区分采样抖动和蓝牙聚包：

```c
Serial_Printf("[data,%lu,angle=%f,estimate=%f]",
              (unsigned long)SampleMilliseconds, AngleAcc, AngleEstimate);
```
