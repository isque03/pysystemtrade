from syscontrol.run_process import processToRun
from sysexecution.stack_handler.stack_handler import stackHandler
from sysdata.data_blob import dataBlob


def run_additional_sampling():
    process_name = "run_additional_sampling"
    data = dataBlob(log_name=process_name)
    list_of_timer_names_and_functions = get_list_of_timer_functions_for_additional_sampling()
    price_process = processToRun(process_name, data, list_of_timer_names_and_functions)
    price_process.run_process()


def get_list_of_timer_functions_for_additional_sampling():
    stack_handler_data = dataBlob(log_name="additional_sampling")
    stack_handler = stackHandler(stack_handler_data)
    list_of_timer_names_and_functions = [
        ("refresh_additional_sampling_all_instruments", stack_handler),
    ]

    return list_of_timer_names_and_functions


if __name__ == "__main__":
    run_additional_sampling()
