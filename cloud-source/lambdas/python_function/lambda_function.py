import json

def lambda_handler(event, context):
    if event['httpMethod'] == 'GET':
        return {
            'statusCode': 200,
            'body': 'Hello~~~'
        }
    if event['httpMethod'] == 'POST':
        req_data = json.loads(event['body']) # JSON 문자열 처리
        return {
            'statusCode': 200,
            'body': json.dumps(req_data)
        }