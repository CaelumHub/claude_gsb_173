# -*- coding: utf-8 -*-
"""
演示数据生成器：首次启动（--seed）时向空数据目录写入若干示例项目，
覆盖 MiniLang 的主要语言特性，便于立即体验编译、调试、剖析与内存可视化。
"""

from . import service as service_mod

# 示例源码集合（每个都刻意覆盖不同特性，便于演示各页面）
EXAMPLES = [
    {
        "name": "斐波那契数列",
        "source": '''// 递归计算斐波那契数列（演示调用栈 / 断点 / 单步）
func fib(n) {
    if (n < 2) {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}

func main_program() {
    for (var i = 0; i < 12; i = i + 1) {
        print("fib(", i, ") =", fib(i));
    }
}

main_program();
''',
    },
    {
        "name": "数组与循环",
        "source": '''// 列表操作与循环（演示内存模型 / 下标访问）
var data = [3, 1, 4, 1, 5, 9, 2, 6];
var sum = 0;

for (var i = 0; i < len(data); i = i + 1) {
    sum += data[i];
}
print("总和 =", sum);
print("平均值 =", sum / len(data));

push(data, 100);
print("追加后长度 =", len(data));
print("最后一个元素 =", data[len(data) - 1]);

var evens = [];
for (var j = 0; j < len(data); j = j + 1) {
    if (data[j] % 2 == 0) {
        push(evens, data[j]);
    }
}
print("偶数 =", evens);
''',
    },
    {
        "name": "排序与性能剖析",
        "source": '''// 冒泡排序（演示性能剖析的热点函数与耗时）
func bubble(arr) {
    var n = len(arr);
    for (var i = 0; i < n - 1; i = i + 1) {
        for (var j = 0; j < n - i - 1; j = j + 1) {
            if (arr[j] > arr[j + 1]) {
                var tmp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = tmp;
            }
        }
    }
    return arr;
}

var numbers = [9, 4, 7, 1, 8, 2, 6, 3, 5, 0];
var sorted = bubble(numbers);
print("排序结果 =", sorted);
print("耗时函数 bubble 被调用 1 次，内部循环次数 =", len(sorted) * (len(sorted) - 1) / 2);
''',
    },
    {
        "name": "错误诊断示例",
        "source": '''// 这段代码包含若干错误，用于演示错误诊断与修复建议
var radius = 5;
var area = 3.14159 * radius * radus;   // 拼写错误：radus -> radius

if (area > 0 {
    print("面积 =", area)              // 缺少右括号
}

var arr = [10, 20, 30];
print(arr[5]);                          // 下标越界（运行时错误）
''',
    },
]


def seed_demo(service=None):
    """写入演示项目，返回创建的项目数。"""
    svc = service or service_mod.Service()
    existing = svc.list_projects()
    if existing:
        return 0  # 已有数据，不重复生成
    created = 0
    for ex in EXAMPLES:
        svc.create_project(ex["name"], ex["source"])
        created += 1
    return created
